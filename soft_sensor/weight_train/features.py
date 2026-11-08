"""Backward-looking per-machine features. ONE entry point for training and prediction:

    X, meta = build_features(hourly, inverter, strokes)

Rows must be hourly and contiguous per machine. A cycle starts at each lift_event
row (the container was emptied at the start of that hour). Every feature at hour t
uses only rows <= t of the same machine and completed calendar days for daily
weather (today's daily total/mean would contain future hours).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .columns import CURVE_COLUMNS, DEVICE_COLUMNS, OPTIONAL_DEVICE_COLUMNS, assert_no_leakage
from .physics import PressureInverter
from .settings import CLASSES, INFEED_EXPOSURE, INFEEDS, MATERIALS, NOMINAL_DENSITY

RAIN_LAGS = range(1, 8)
CATEGORICAL = {"material": MATERIALS, "equipment_class": CLASSES, "infeed": INFEEDS}

MACHINE = ["container_volume_m3", "equipment_class", "infeed", "drainage"]
CALENDAR = ["hour", "weekday"]
CYCLE = ["hours_since_lift"]
FILL = ["fill_ffill", "fill_max_cycle", "fill_change_24h", "naive_kg_m3"]
PRESSES = ["presses", "presses_cycle", "presses_per_fill"]
PRESSURE = ["p_now", "p_last", "p_ewm", "p_max_cycle", "p_count_cycle",
            "rho_p", "rho_p_smooth", "pressure_kg_m3"]
STROKES = ["stroke_peak_mean", "stroke_duration_mean", "energy_kwh"]
GRAPH = ["curve_contact_pos", "curve_contact_ewm", "curve_contact_min_cycle", "curve_rise_slope_ewm",
         "curve_mean_bar_ewm", "curve_completion_min_cycle", "graph_kg_m3"]
WEATHER = (["temp_c", "rh_prev_day", "rh_mean_cycle"] + [f"rain_mm_d{k}" for k in RAIN_LAGS]
           + ["rain_72h", "rain_exposed_cycle", "frost_days_cycle"])

FEATURE_SETS = {
    "fill_presses": MACHINE + CALENDAR + CYCLE + FILL + PRESSES,
    "pressure": MACHINE + CALENDAR + CYCLE + FILL + PRESSES + PRESSURE + STROKES,
    "pressure_graph": MACHINE + CALENDAR + CYCLE + FILL + PRESSES + PRESSURE + STROKES + GRAPH,
    "full": MACHINE + CALENDAR + CYCLE + FILL + PRESSES + PRESSURE + STROKES + GRAPH + WEATHER,
}
META = ["bin_id", "ts", "material", "container_volume_m3", "lift_event", "seg", "cycle"]


@dataclass
class Features:
    X: pd.DataFrame       # model inputs (all feature-set columns + material)
    meta: pd.DataFrame    # bookkeeping, never a model input


def _prepare(hourly: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in DEVICE_COLUMNS if c not in hourly.columns]
    if missing:
        raise ValueError(f"hourly data lacks required columns: {missing}")
    cols = list(DEVICE_COLUMNS) + [c for c in OPTIONAL_DEVICE_COLUMNS if c in hourly.columns]
    h = hourly[cols].sort_values(["bin_id", "ts"], kind="stable").reset_index(drop=True)  # whitelist only
    h["lift_event"] = h["lift_event"].fillna(0).astype(np.int8)
    return h


def _segments(h: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """seg: id of (machine, cycle); cycle: lifts seen so far on this machine."""
    new_bin = h["bin_id"].ne(h["bin_id"].shift()).to_numpy()
    seg = np.cumsum(new_bin | (h["lift_event"].to_numpy() == 1))
    cycle = h.groupby("bin_id", sort=False)["lift_event"].cumsum().to_numpy()
    return seg, cycle


def _ewm(values: pd.Series, seg: np.ndarray, halflife: float) -> pd.Series:
    """EWMA over valid observations inside each segment, carried over NaN rows."""
    out = values.groupby(seg).transform(lambda s: s.ewm(halflife=halflife, ignore_na=True).mean())
    return out.groupby(seg).ffill()


def _fill_features(h, seg, f) -> None:
    fill = h["fill_pct"].groupby(seg).ffill()
    f["fill_ffill"] = fill
    f["fill_max_cycle"] = fill.groupby(seg).cummax()
    f["fill_change_24h"] = fill - fill.groupby(h["bin_id"].to_numpy()).shift(24)
    f["naive_kg_m3"] = fill / 100.0 * h["material"].map(NOMINAL_DENSITY).to_numpy()
    f["presses"] = h["presses"].to_numpy()
    f["presses_cycle"] = h["presses"].groupby(seg).cumsum()
    f["presses_per_fill"] = f["presses_cycle"] / np.maximum(fill, 5.0)


def _pressure_features(h, seg, f) -> None:
    p = h["peak_pressure_bar"]
    f["p_now"] = p.to_numpy()
    f["p_last"] = p.groupby(seg).ffill()
    f["p_ewm"] = _ewm(p, seg, halflife=6.0)
    f["p_max_cycle"] = p.fillna(-np.inf).groupby(seg).cummax().replace(-np.inf, np.nan)
    f["p_count_cycle"] = p.notna().groupby(seg).cumsum()
    f["energy_kwh"] = h["energy_kwh"].to_numpy() if "energy_kwh" in h else np.nan


def _graph_features(h, seg, f) -> None:
    """Pressure graph (per-stroke curve features, hourly mean) smoothed within the cycle."""
    if not all(c in h for c in CURVE_COLUMNS):
        for col in GRAPH:
            f[col] = np.nan
        return
    contact = h["curve_contact_pos"]
    f["curve_contact_pos"] = contact.to_numpy()
    f["curve_contact_ewm"] = _ewm(contact, seg, halflife=4.0)
    f["curve_contact_min_cycle"] = contact.fillna(np.inf).groupby(seg).cummin().replace(np.inf, np.nan)
    f["curve_rise_slope_ewm"] = _ewm(h["curve_rise_slope_bar"], seg, halflife=4.0)
    f["curve_mean_bar_ewm"] = _ewm(h["curve_mean_bar"], seg, halflife=4.0)
    f["curve_completion_min_cycle"] = h["curve_completion"].fillna(np.inf).groupby(seg).cummin().replace(np.inf, np.nan)
    # geometric fill from the contact point (onset 0.30, span 0.70 nominal) x nominal density
    geo_fill = np.clip(0.30 + (1.0 - f["curve_contact_ewm"]), 0.0, 1.0)   # contact 1 -> 30 %, 0.3 -> 100 %
    f["graph_kg_m3"] = geo_fill * h["material"].map(NOMINAL_DENSITY).to_numpy()


def _daily_weather(h: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """Per (machine, day) table with lagged rain/RH of completed days."""
    day = h["ts"].dt.tz_localize(None).dt.normalize()
    keys = [h["bin_id"], day]
    daily = h.groupby(keys, sort=False).agg(precip=("precip_mm_day", "first"), rh=("rh_pct", "first"))
    row_of = h.groupby(keys, sort=False).ngroup().to_numpy()
    for k in RAIN_LAGS:
        idx = pd.MultiIndex.from_arrays([daily.index.get_level_values(0),
                                         daily.index.get_level_values(1) - pd.Timedelta(days=k)])
        daily[f"rain_mm_d{k}"] = daily["precip"].reindex(idx).to_numpy()
        if k == 1:
            daily["rh_prev_day"] = daily["rh"].reindex(idx).to_numpy()
    return daily, row_of


def _weather_features(h, seg, f) -> None:
    daily, row_of = _daily_weather(h)
    for col in ["rh_prev_day"] + [f"rain_mm_d{k}" for k in RAIN_LAGS]:
        f[col] = daily[col].to_numpy()[row_of]
    f["temp_c"] = h["temp_c"].to_numpy()
    f["rh_mean_cycle"] = f["rh_prev_day"].groupby(seg).transform(lambda s: s.expanding().mean())
    if "precip_mm" in h:
        rain_h = h["precip_mm"].fillna(0.0)
        cum = rain_h.groupby(h["bin_id"].to_numpy()).cumsum()
        f["rain_72h"] = cum - cum.groupby(h["bin_id"].to_numpy()).shift(72).fillna(0.0)
    else:  # fall back to completed days only
        rain_h = (f["rain_mm_d1"] / 24.0).fillna(0.0)
        f["rain_72h"] = f[["rain_mm_d1", "rain_mm_d2", "rain_mm_d3"]].sum(axis=1, min_count=1)
    exposure = h["infeed"].map(INFEED_EXPOSURE).to_numpy()
    f["rain_exposed_cycle"] = rain_h.groupby(seg).cumsum() * exposure
    f["frost_days_cycle"] = (h["temp_c"] < 0).groupby(seg).cumsum() / 24.0


def _stroke_features(h, strokes: pd.DataFrame | None, f) -> None:
    if strokes is None or len(strokes) == 0:
        f["stroke_peak_mean"] = np.nan
        f["stroke_duration_mean"] = np.nan
        return
    s = strokes.assign(ts=strokes["ts"].dt.floor("h"), bin_id=strokes["bin_id"].astype(str))
    agg = s.groupby(["bin_id", "ts"]).agg(stroke_peak_mean=("peak_pressure_bar", "mean"),
                                          stroke_duration_mean=("duration_s", "mean"))
    key = pd.MultiIndex.from_arrays([h["bin_id"].astype(str), h["ts"]])
    joined = agg.reindex(key)
    f["stroke_peak_mean"] = joined["stroke_peak_mean"].to_numpy()
    f["stroke_duration_mean"] = joined["stroke_duration_mean"].to_numpy()


def _machine_calendar(h, f) -> None:
    for col, levels in CATEGORICAL.items():
        f[col] = pd.Categorical(h[col].astype(str), categories=list(levels))
    f["container_volume_m3"] = h["container_volume_m3"].astype(float).to_numpy()
    f["drainage"] = h["drainage"].astype(int).to_numpy()
    local = h["ts"].dt.tz_localize(None) if h["ts"].dt.tz is not None else h["ts"]
    f["hour"] = local.dt.hour.to_numpy()
    f["weekday"] = local.dt.day_of_week.to_numpy()


def base_features(hourly: pd.DataFrame, strokes: pd.DataFrame | None = None) -> Features:
    """All features except the pressure-implied density (which needs a fitted inverter)."""
    h = _prepare(hourly)
    seg, cycle = _segments(h)
    f = pd.DataFrame(index=h.index)
    f["hours_since_lift"] = h.groupby(seg).cumcount().to_numpy()
    _fill_features(h, seg, f)
    _pressure_features(h, seg, f)
    _stroke_features(h, strokes, f)
    _graph_features(h, seg, f)
    _weather_features(h, seg, f)
    _machine_calendar(h, f)
    meta = pd.DataFrame({"bin_id": h["bin_id"].to_numpy(), "ts": h["ts"], "material": h["material"].to_numpy(),
                         "container_volume_m3": f["container_volume_m3"].to_numpy(),
                         "lift_event": h["lift_event"].to_numpy(), "seg": seg, "cycle": cycle})
    return Features(X=f, meta=meta)


def add_pressure_density(feats: Features, inverter: PressureInverter) -> Features:
    X = feats.X.copy()
    seg = feats.meta["seg"].to_numpy()
    # viscosity correction with the air temperature (no oil temperature input)
    rho = inverter.density(X["p_now"].to_numpy(), X["fill_ffill"].to_numpy(), X["temp_c"].to_numpy(),
                           feats.meta["material"].to_numpy())
    X["rho_p"] = rho
    X["rho_p_smooth"] = _ewm(pd.Series(rho, index=X.index), seg, halflife=4.0)
    X["pressure_kg_m3"] = X["fill_ffill"] / 100.0 * X["rho_p_smooth"]
    numeric = [c for c in X.columns if c not in CATEGORICAL]
    X[numeric] = X[numeric].astype(np.float32)
    assert_no_leakage(X.columns)
    return Features(X=X, meta=feats.meta)


def build_features(hourly: pd.DataFrame, inverter: PressureInverter,
                   strokes: pd.DataFrame | None = None) -> Features:
    """The single feature pipeline used by training and prediction."""
    return add_pressure_density(base_features(hourly, strokes), inverter)


def pre_lift_mask(meta: pd.DataFrame) -> np.ndarray:
    """Rows whose next hour (same machine) is a lift: the value compared to the weighbridge.

    Uses the future lift_event, so it is for selecting evaluation/weighting rows only.
    """
    nxt_lift = meta["lift_event"].shift(-1).fillna(0).to_numpy() == 1
    same_bin = meta["bin_id"].shift(-1).to_numpy() == meta["bin_id"].to_numpy()
    return nxt_lift & same_bin
