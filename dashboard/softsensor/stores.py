"""Data access for the soft-sensor service.

Telemetry (what a real compactor + SMART + public weather can provide) is kept strictly apart
from simulator truth, which is loaded only to *display* evaluation next to the estimates.
Only the replay window (the model's held-out test period) plus a look-back for features is held
in memory, about 1.2 M machine-hours for the 400-machine run.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Settings

log = logging.getLogger(__name__)

TZ = "Europe/Helsinki"
TRUTH_COLUMNS = ("true_mass_kg", "true_fill_pct")
STROKE_COLUMNS = ["bin_id", "ts", "peak_pressure_bar", "duration_s"]
# bins.parquet columns a customer/Europress would know from the installation record.
# Hidden simulator truth (density_factor, sensor biases, thresholds, ...) is never loaded.
PUBLIC_MACHINE_COLUMNS = ["bin_id", "site_id", "material", "equipment_class", "container_volume_m3",
                          "container_tare_kg", "region", "sector", "infeed", "drainage", "press_model",
                          "force_t", "motor_kw", "waterproof", "indoor_installation", "floating_roof"]
LIFT_COLUMNS = ["bin_id", "ts", "order_ts", "trigger", "weighbridge_net_kg", "gross_vehicle_load_kg"]
LIFT_TRUTH_COLUMNS = ["true_mass_removed_kg"]


@dataclass(frozen=True)
class FleetGrid:
    """Hour-by-machine matrices over the replay window (rows = bins, columns = hours)."""
    bins: np.ndarray            # bin ids, sorted
    hours: pd.DatetimeIndex     # replay hours, tz-aware Europe/Helsinki
    pred: np.ndarray            # batch estimate, kg
    p10: np.ndarray
    p90: np.ndarray
    fill: np.ndarray            # firmware fill estimate, %, forward-filled over dropouts
    hours_since_lift: np.ndarray
    truth: np.ndarray           # simulator truth, kg (evaluation display only)

    def bin_index(self, bin_id: str) -> int | None:
        i = int(np.searchsorted(self.bins, bin_id))
        return i if i < len(self.bins) and self.bins[i] == bin_id else None

    def hour_index(self, at: pd.Timestamp) -> int | None:
        i = int(self.hours.searchsorted(at))
        return i if i < len(self.hours) and self.hours[i] == at else None


@dataclass(frozen=True)
class SensorData:
    telemetry: pd.DataFrame     # device columns only, sorted by bin_id, ts
    tel_ranges: dict            # bin_id -> (start, stop) row slice in telemetry
    strokes: pd.DataFrame | None
    stroke_ranges: dict
    machines: pd.DataFrame      # public installation attributes
    lifts: pd.DataFrame         # emptyings: weighbridge tickets (+ truth columns, prefixed true_)
    grid: FleetGrid
    test_start: pd.Timestamp    # model's held-out period starts here
    info: dict

    @property
    def replay_start(self) -> pd.Timestamp:
        return self.grid.hours[0]

    @property
    def replay_end(self) -> pd.Timestamp:
        return self.grid.hours[-1]

    def bin_telemetry(self, bin_id: str) -> pd.DataFrame:
        start, stop = self.tel_ranges[bin_id]
        return self.telemetry.iloc[start:stop]

    def bin_strokes(self, bin_id: str) -> pd.DataFrame | None:
        if self.strokes is None or bin_id not in self.stroke_ranges:
            return None
        start, stop = self.stroke_ranges[bin_id]
        return self.strokes.iloc[start:stop]


# ---------------------------------------------------------------------------- helpers
def row_ranges(bin_ids: pd.Series) -> dict:
    """bin_id -> (start, stop) for a frame sorted by bin_id."""
    values = bin_ids.to_numpy()
    change = np.flatnonzero(values[1:] != values[:-1]) + 1
    starts = np.concatenate([[0], change])
    stops = np.concatenate([change, [len(values)]])
    return {str(values[s]): (int(s), int(e)) for s, e in zip(starts, stops)} if len(values) else {}


def cycle_ids(bin_ids: np.ndarray, lift_event: np.ndarray) -> np.ndarray:
    """Id of (machine, cycle): a new cycle starts at each lift row (same rule as weight_train.features)."""
    new_bin = np.concatenate([[True], bin_ids[1:] != bin_ids[:-1]]) if len(bin_ids) else np.array([], bool)
    return np.cumsum(new_bin | (lift_event == 1))


def hours_since_lift(bin_ids: np.ndarray, lift_event: np.ndarray) -> np.ndarray:
    """Hours since the last lift_event row of the same machine (0 on the lift hour, NaN before any)."""
    n = len(lift_event)
    pos = np.arange(n, dtype=float)
    last = pd.Series(np.where(lift_event == 1, pos, np.nan)).groupby(bin_ids).ffill().to_numpy()
    return pos - last


def read_test_start(settings: Settings) -> pd.Timestamp:
    metrics_path = settings.model_dir / "metrics.json"
    if metrics_path.is_file():
        metrics = json.loads(metrics_path.read_text())
        start = metrics.get("splits", {}).get(settings.split, {}).get("test_start")
        if start:
            return pd.Timestamp(start).tz_convert(TZ)
    raise ValueError(f"no test_start for split {settings.split!r} in {metrics_path}")


def _read_parquet(path: Path, columns: list[str], since: pd.Timestamp | None = None) -> pd.DataFrame:
    filters = [("ts", ">=", since)] if since is not None else None
    df = pd.read_parquet(path, columns=columns, filters=filters)
    if "bin_id" in df:
        df["bin_id"] = df["bin_id"].astype(str)
    return df


# ---------------------------------------------------------------------------- loaders
def load_telemetry(settings: Settings, since: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(telemetry, truth) aligned row by row. Telemetry passes the model's whitelist + leakage guard."""
    from weight_train.columns import DEVICE_COLUMNS, OPTIONAL_DEVICE_COLUMNS, assert_no_leakage

    path = settings.run_dir / "hourly.parquet"
    available = _schema(path)
    device = [c for c in (*DEVICE_COLUMNS, *OPTIONAL_DEVICE_COLUMNS) if c in available]
    truth = [c for c in TRUTH_COLUMNS if c in available]
    frame = _read_parquet(path, device + truth, since)
    frame = frame.sort_values(["bin_id", "ts"], kind="stable").reset_index(drop=True)
    telemetry = frame[device]
    assert_no_leakage(telemetry.columns)
    return telemetry, frame[["bin_id", "ts", *truth]]


def _schema(path: Path) -> set[str]:
    import pyarrow.parquet as pq
    return set(pq.ParquetFile(path).schema_arrow.names)


def load_strokes(settings: Settings, since: pd.Timestamp) -> pd.DataFrame | None:
    path = settings.run_dir / "strokes.parquet"
    if not path.is_file():
        log.warning("no strokes.parquet: per-stroke features will be NaN (slightly less accurate)")
        return None
    strokes = _read_parquet(path, STROKE_COLUMNS, since)
    return strokes.sort_values(["bin_id", "ts"], kind="stable").reset_index(drop=True)


def load_machines(settings: Settings) -> pd.DataFrame:
    path = settings.run_dir / "bins.parquet"
    cols = [c for c in PUBLIC_MACHINE_COLUMNS if c in _schema(path)]
    return _read_parquet(path, cols).sort_values("bin_id").reset_index(drop=True)


def load_lifts(settings: Settings, since: pd.Timestamp) -> pd.DataFrame:
    path = settings.run_dir / "lifts.parquet"
    cols = [c for c in LIFT_COLUMNS + LIFT_TRUTH_COLUMNS if c in _schema(path)]
    lifts = _read_parquet(path, cols, since)
    return lifts.sort_values(["bin_id", "ts"]).reset_index(drop=True)


def load_batch(settings: Settings, since: pd.Timestamp) -> pd.DataFrame | None:
    """Output of the hourly batch scoring job (``weight_train predict``), if it has been run."""
    path = settings.model_dir / "predictions.parquet"
    if not path.is_file():
        return None
    cols = ["bin_id", "ts", "mass_pred_kg", "mass_p10_kg", "mass_p90_kg"]
    return _read_parquet(path, cols, since)


def build_grid(telemetry: pd.DataFrame, truth: pd.DataFrame, batch: pd.DataFrame,
               replay_start: pd.Timestamp) -> FleetGrid:
    bin_ids = telemetry["bin_id"].to_numpy()
    lift = telemetry["lift_event"].fillna(0).to_numpy()
    hsl = hours_since_lift(bin_ids, lift)
    # forward-fill dropouts within a cycle only (as the model's fill_ffill): never carry a pre-emptying fill over
    fill = telemetry["fill_pct"].groupby(cycle_ids(bin_ids, lift)).ffill().to_numpy()
    base = pd.DataFrame({"bin_id": telemetry["bin_id"].to_numpy(), "ts": telemetry["ts"].to_numpy(),
                         "fill": fill, "hsl": hsl, "truth": truth["true_mass_kg"].to_numpy()
                         if "true_mass_kg" in truth else np.nan})
    base = base[base["ts"] >= replay_start]
    bins = np.array(sorted(base["bin_id"].unique()))
    hours = pd.DatetimeIndex(sorted(base["ts"].unique())).tz_convert(TZ)

    def matrix(df: pd.DataFrame, col: str) -> np.ndarray:
        wide = df.pivot(index="bin_id", columns="ts", values=col)
        wide.columns = pd.DatetimeIndex(wide.columns).tz_convert(TZ)
        return wide.reindex(index=bins, columns=hours).to_numpy(dtype=np.float32)

    batch = batch[batch["ts"] >= replay_start]
    return FleetGrid(bins=bins, hours=hours,
                     pred=matrix(batch, "mass_pred_kg"), p10=matrix(batch, "mass_p10_kg"),
                     p90=matrix(batch, "mass_p90_kg"), fill=matrix(base, "fill"),
                     hours_since_lift=matrix(base, "hsl"), truth=matrix(base, "truth"))


def load_all(settings: Settings, score_batch) -> SensorData:
    """Load everything the service needs. ``score_batch(telemetry, strokes)`` is used only when no
    batch predictions exist (it runs the same model over the loaded telemetry)."""
    test_start = read_test_start(settings)
    replay_start = test_start.ceil("h")
    since = (replay_start - pd.Timedelta(days=settings.lookback_days)).normalize()
    telemetry, truth = load_telemetry(settings, since)
    strokes = load_strokes(settings, since)
    # predictions.parquet is written by `weight_train predict` with its default (time) bundle; for any other
    # split re-score with the configured bundle so live and batch values come from the same model
    batch = load_batch(settings, replay_start) if settings.split == "time" else None
    source = "predictions.parquet (batch job)"
    if batch is None:
        batch = score_batch(telemetry, strokes)
        source = "scored at start-up (no predictions.parquet)"
    grid = build_grid(telemetry, truth, batch, replay_start)
    lifts = load_lifts(settings, since)
    info = {"run": settings.run, "models": settings.models, "split": settings.split,
            "telemetry_rows": int(len(telemetry)), "stroke_rows": int(0 if strokes is None else len(strokes)),
            "batch_source": source, "machines": int(len(grid.bins)), "hours": int(len(grid.hours))}
    log.info("loaded %s", info)
    return SensorData(telemetry=telemetry, tel_ranges=row_ranges(telemetry["bin_id"]),
                      strokes=strokes, stroke_ranges=row_ranges(strokes["bin_id"]) if strokes is not None else {},
                      machines=load_machines(settings), lifts=lifts, grid=grid, test_start=test_start, info=info)
