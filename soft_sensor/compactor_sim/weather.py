"""Regional weather: daily synthesis (or FMI CSV replay) and hourly disaggregation.

Daily: temperature = seasonal sine + spatially correlated AR(1) anomaly;
precipitation = two-state Markov chain (wet persistence, Gaussian-copula spatial
correlation) with gamma wet-day amounts; RH = seasonal cosine + wet-day bump + AR(1).
Hourly: interpolated daily means + diurnal cycle, showery rain blocks, snow when
T < 0 C, regional snowpack (degree-day melt), yard wetness, indoor RH.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .timeline import Timeline

_ERF = np.vectorize(math.erf)
YEAR_DAYS = 365.25


def _season(doy: np.ndarray, peak_doy: float) -> np.ndarray:
    """cos wave: +1 at ``peak_doy``, -1 half a year later."""
    return np.cos(2 * np.pi * (doy - peak_doy) / YEAR_DAYS)


def ema(x: np.ndarray, tau: float) -> np.ndarray:
    """Exponential moving average along axis 0 with time constant ``tau`` (steps)."""
    alpha = 1.0 - math.exp(-1.0 / tau)
    out = np.empty_like(x, dtype=np.float64)
    out[0] = x[0]
    for t in range(1, len(x)):
        out[t] = out[t - 1] + alpha * (x[t] - out[t - 1])
    return out


def correlated_ar1(n_t: int, n_r: int, phi: float, corr: float, rng: np.random.Generator) -> np.ndarray:
    """Unit-variance AR(1) series per column with spatially correlated innovations."""
    z = math.sqrt(corr) * rng.standard_normal((n_t, 1)) + math.sqrt(1 - corr) * rng.standard_normal((n_t, n_r))
    x = np.empty((n_t, n_r))
    x[0] = z[0]
    scale = math.sqrt(1 - phi**2)
    for t in range(1, n_t):
        x[t] = phi * x[t - 1] + scale * z[t]
    return x


def _region_param(wcfg: Mapping[str, Any], regions: tuple[str, ...], key: str) -> np.ndarray:
    return np.array([float(wcfg["regions"][r][key]) for r in regions])


# ----------------------------------------------------------------------------- daily synthesis
def synth_daily_temperature(wcfg, regions, doy, rng) -> np.ndarray:
    tc = wcfg["temperature"]
    jan, jul = _region_param(wcfg, regions, "jan_c"), _region_param(wcfg, regions, "jul_c")
    warm = _season(doy, tc["coldest_doy"] + YEAR_DAYS / 2)[:, None]
    clim = (jan + jul) / 2 + (jul - jan) / 2 * warm
    sd = tc["anomaly_sd_summer_c"] + (tc["anomaly_sd_winter_c"] - tc["anomaly_sd_summer_c"]) * (1 - warm) / 2
    anomaly = correlated_ar1(len(doy), len(regions), tc["anomaly_ar1_phi"], tc["spatial_corr"], rng)
    return clim + sd * anomaly


def synth_daily_precip(wcfg, regions, doy, rng) -> np.ndarray:
    pc = wcfg["precipitation"]
    p_mean = _region_param(wcfg, regions, "wet_day_prob")
    annual = _region_param(wcfg, regions, "precip_mm_year")
    season = _season(doy, pc["peak_doy"])[:, None]
    p_wet = np.clip(p_mean * (1 + pc["seasonal_amp_prob"] * season), 0.02, 0.95)
    p01 = p_wet * (1 - pc["persistence"])
    p11 = p01 + pc["persistence"]
    z = correlated_ar1(len(doy), len(regions), 0.0, pc["spatial_corr"], rng)
    u = 0.5 * (1 + _ERF(z / math.sqrt(2)))
    wet = np.zeros(z.shape, dtype=bool)
    wet[0] = u[0] < p_wet[0]
    for d in range(1, len(doy)):
        wet[d] = u[d] < np.where(wet[d - 1], p11[d], p01[d])
    year = _season(np.arange(1, 366), pc["peak_doy"])
    norm = np.mean((1 + pc["seasonal_amp_prob"] * year) * (1 + pc["seasonal_amp_amount"] * year))
    mean_amt = annual / (YEAR_DAYS * p_mean) * (1 + pc["seasonal_amp_amount"] * season) / norm
    k = pc["gamma_shape"]
    amount = np.maximum(rng.gamma(k, mean_amt / k), pc["min_wet_mm"])
    return np.where(wet, amount, 0.0)


def synth_daily_rh(wcfg, regions, doy, precip, rng) -> np.ndarray:
    hc = wcfg["humidity"]
    base = _region_param(wcfg, regions, "rh_mean") + _region_param(wcfg, regions, "rh_amp") * _season(doy, hc["peak_doy"])[:, None]
    noise = hc["noise_sd_pct"] * correlated_ar1(len(doy), len(regions), hc["ar1_phi"], 0.5, rng)
    return np.clip(base + hc["wet_day_bump_pct"] * (precip > 0) + noise, *hc["clip_pct"])


def load_fmi_daily(wcfg, regions, tl: Timeline) -> dict[str, np.ndarray | None]:
    """Read daily FMI observations; returns (D, R) arrays (rh may be None)."""
    fc = wcfg["fmi_csv"]
    path = Path(fc["path"])
    if not path.is_file():
        raise FileNotFoundError(f"FMI CSV not found: {path}")
    inv = {v: k for k, v in fc["columns"].items()}
    df = pd.read_csv(path).rename(columns=inv)
    missing = {"region", "date", "temp_mean_c", "precip_mm"} - set(df.columns)
    if missing:
        raise ValueError(f"FMI CSV lacks columns {sorted(missing)}")
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    out: dict[str, np.ndarray | None] = {}
    for var in ("temp_mean_c", "precip_mm", "rh_pct"):
        if var not in df.columns:
            out[var] = None
            continue
        wide = df.pivot_table(index="date", columns="region", values=var, aggfunc="mean")
        wide = wide.reindex(index=tl.dates, columns=list(regions))
        if wide.isna().any().any():
            n_missing = int(wide.isna().sum().sum())
            raise ValueError(f"FMI CSV: {var} missing for {n_missing} region-days in the simulated span")
        out[var] = wide.to_numpy(dtype=float)
    return out


# ----------------------------------------------------------------------------- hourly
def hourly_temperature(t_day, tl: Timeline, tc, rng) -> np.ndarray:
    hours = np.arange(tl.n_hours)
    centers = tl.day_start + (tl.day_len - 1) / 2
    interp = np.column_stack([np.interp(hours, centers, t_day[:, r]) for r in range(t_day.shape[1])])
    warm = _season(tl.doy[tl.day_idx], tc["coldest_doy"] + YEAR_DAYS / 2)
    amp = tc["diurnal_amp_winter_c"] + (tc["diurnal_amp_summer_c"] - tc["diurnal_amp_winter_c"]) * (1 + warm) / 2
    diurnal = amp * np.cos(2 * np.pi * (tl.hour - tc["warmest_hour"]) / 24)
    noise = tc["hourly_noise_sd_c"] * correlated_ar1(tl.n_hours, t_day.shape[1], tc["hourly_noise_phi"], 0.5, rng)
    x = interp + diurnal[:, None] + noise
    return x + (t_day - _daily_mean(x, tl))[tl.day_idx]  # exact daily means


def _daily_mean(x: np.ndarray, tl: Timeline) -> np.ndarray:
    return np.add.reduceat(x, tl.day_start, axis=0) / tl.day_len[:, None]


def hourly_precip(p_day, tl: Timeline, pc, rng) -> np.ndarray:
    """Distribute each wet day's total over a block of showery hours."""
    out = np.zeros((tl.n_hours, p_day.shape[1]))
    for d, r in zip(*np.nonzero(p_day > 0)):
        amount, length = p_day[d, r], int(tl.day_len[d])
        n = pc["hours_base"] + pc["hours_per_sqrt_mm"] * math.sqrt(amount) + rng.exponential(1.0)
        n = int(np.clip(round(n), 1, length))
        hours = (rng.integers(length) + np.arange(n)) % length
        w = rng.gamma(pc["dirichlet_alpha"], 1.0, n) + 1e-12
        out[tl.day_start[d] + hours, r] += amount * w / w.sum()
    return out


def hourly_rh(rh_day, precip_h, tl: Timeline, wcfg) -> np.ndarray:
    hc, tc = wcfg["humidity"], wcfg["temperature"]
    warm = _season(tl.doy[tl.day_idx], tc["coldest_doy"] + YEAR_DAYS / 2)
    amp = hc["diurnal_amp_winter_pct"] + (hc["diurnal_amp_summer_pct"] - hc["diurnal_amp_winter_pct"]) * (1 + warm) / 2
    diurnal = -amp * np.cos(2 * np.pi * (tl.hour - tc["warmest_hour"]) / 24)
    rh = rh_day[tl.day_idx] + diurnal[:, None] + hc["rain_hour_bump_pct"] * (precip_h > 0)
    return np.clip(rh, 15.0, 100.0)


def snowpack(snow_h, temp_h, ddf_mm_per_c_day: float) -> tuple[np.ndarray, np.ndarray]:
    """Regional snow water equivalent and hourly melt (degree-hour model)."""
    swe = np.zeros_like(snow_h)
    melt = np.zeros_like(snow_h)
    store = np.zeros(snow_h.shape[1])
    rate = ddf_mm_per_c_day / 24.0
    for t in range(len(snow_h)):
        store = store + snow_h[t]
        melt[t] = np.minimum(store, rate * np.maximum(temp_h[t], 0.0))
        store = store - melt[t]
        swe[t] = store
    return swe, melt


def yard_wetness(rain_h, melt_h, ycfg) -> np.ndarray:
    """0..1 index: how wet the yard/route to the hopper is (decaying water store)."""
    decay = math.exp(-1.0 / ycfg["wet_decay_h"])
    store = np.zeros(rain_h.shape[1])
    out = np.zeros_like(rain_h)
    for t in range(len(rain_h)):
        store = store * decay + rain_h[t] + melt_h[t]
        out[t] = np.minimum(1.0, store / ycfg["wet_ref_mm"])
    return out


def saturation_vapour_hpa(temp_c: np.ndarray) -> np.ndarray:
    """Magnus formula over water."""
    return 6.112 * np.exp(17.62 * temp_c / (243.12 + temp_c))


def indoor_rh(temp_h, rh_h, icfg) -> np.ndarray:
    """Heated indoor air: outdoor vapour pressure at indoor temperature + moisture source."""
    vapour = rh_h / 100.0 * saturation_vapour_hpa(temp_h)
    t_in = np.maximum(icfg["temp_c"], temp_h)
    rh_in = 100.0 * vapour / saturation_vapour_hpa(t_in) + icfg["moisture_source_pct"]
    return np.clip(rh_in, *icfg["clip_pct"])


# ----------------------------------------------------------------------------- assembly
@dataclass(frozen=True)
class Weather:
    regions: tuple[str, ...]
    daily: pd.DataFrame          # region, date, temp/precip/snow/rh daily values
    temp_h: np.ndarray           # (T, R) air temperature, C
    precip_h: np.ndarray         # (T, R) mm/h
    rain_h: np.ndarray
    snow_h: np.ndarray
    rh_h: np.ndarray             # outdoor RH %
    rh_in_h: np.ndarray          # indoor (heated) RH %
    melt_h: np.ndarray           # snowpack melt mm/h
    yard_wet: np.ndarray         # 0..1
    rh_day: np.ndarray           # (D, R)
    precip_day: np.ndarray       # (D, R)
    snow_day: np.ndarray         # (D, R)


def _daily_inputs(cfg, regions, tl, rng):
    wcfg = cfg["weather"]
    t_day = synth_daily_temperature(wcfg, regions, tl.doy, rng)
    p_day = synth_daily_precip(wcfg, regions, tl.doy, rng)
    rh_day = synth_daily_rh(wcfg, regions, tl.doy, p_day, rng)
    if wcfg["mode"] == "fmi_csv":
        obs = load_fmi_daily(wcfg, regions, tl)
        t_day, p_day = obs["temp_mean_c"], obs["precip_mm"]
        rh_day = obs["rh_pct"] if obs["rh_pct"] is not None else synth_daily_rh(wcfg, regions, tl.doy, p_day, rng)
    return t_day, p_day, rh_day


def _daily_table(regions, tl, t_day, p_day, rh_day, temp_h, snow_h, swe) -> pd.DataFrame:
    snow_day = np.add.reduceat(snow_h, tl.day_start, axis=0)
    t_min = np.minimum.reduceat(temp_h, tl.day_start, axis=0)
    t_max = np.maximum.reduceat(temp_h, tl.day_start, axis=0)
    end_idx = tl.day_start + tl.day_len - 1
    frames = []
    for r, region in enumerate(regions):
        frames.append(pd.DataFrame({
            "region": region,
            "date": tl.dates,
            "temp_mean_c": t_day[:, r],
            "temp_min_c": t_min[:, r],
            "temp_max_c": t_max[:, r],
            "precip_mm": p_day[:, r],
            "rain_mm": p_day[:, r] - snow_day[:, r],
            "snow_mm": snow_day[:, r],
            "rh_pct": rh_day[:, r],
            "snow_swe_mm": swe[end_idx, r],
        }))
    return pd.concat(frames, ignore_index=True)


def build_weather(cfg: Mapping[str, Any], tl: Timeline, rng: np.random.Generator) -> Weather:
    wcfg = cfg["weather"]
    regions = tuple(wcfg["regions"])
    t_day, p_day, rh_day = _daily_inputs(cfg, regions, tl, rng)
    temp_h = hourly_temperature(t_day, tl, wcfg["temperature"], rng)
    precip_h = hourly_precip(p_day, tl, wcfg["precipitation"], rng)
    snow_h = np.where(temp_h < wcfg["precipitation"]["snow_below_c"], precip_h, 0.0)
    rain_h = precip_h - snow_h
    rh_h = hourly_rh(rh_day, precip_h, tl, wcfg)
    swe, melt_h = snowpack(snow_h, temp_h, wcfg["snowpack"]["degree_day_mm_per_c_day"])
    daily = _daily_table(regions, tl, t_day, p_day, rh_day, temp_h, snow_h, swe)
    return Weather(
        regions=regions, daily=daily, temp_h=temp_h, precip_h=precip_h, rain_h=rain_h,
        snow_h=snow_h, rh_h=rh_h, rh_in_h=indoor_rh(temp_h, rh_h, wcfg["humidity"]["indoor"]),
        melt_h=melt_h, yard_wet=yard_wetness(rain_h, melt_h, wcfg["yard"]),
        rh_day=rh_day, precip_day=p_day, snow_day=np.add.reduceat(snow_h, tl.day_start, axis=0),
    )
