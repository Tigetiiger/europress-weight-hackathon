"""Live inference: run the trained soft sensor on one machine's telemetry up to a given hour.

This is exactly what an on-line deployment does every hour: take the machine's rows since its
last emptying (plus 8 days for the completed-day weather lags), build the backward-looking
features and predict. The result must equal the batch job's value for the same hour.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

import numpy as np
import pandas as pd

from .stores import SensorData

WEATHER_LOOKBACK = pd.Timedelta(days=8)     # rain d1..d7 + RH of the previous completed day
MAX_CONCURRENT = 4                          # bound CPU/RAM under load
SLOT_WAIT_S = 3.0                           # then give up with 503 instead of queueing behind abandoned requests


class BusyError(RuntimeError):
    """All inference slots stayed busy for SLOT_WAIT_S."""
# Features shown to the user as "what the model saw" (all backward-looking device signals).
DISPLAY_FEATURES = ("hours_since_lift", "fill_ffill", "presses_cycle", "p_ewm", "p_max_cycle",
                    "pressure_kg_m3", "curve_contact_ewm", "oil_temp_c", "temp_c",
                    "rain_72h", "rain_exposed_cycle", "frost_days_cycle", "naive_kg_m3")


def _num(v) -> float | None:
    return None if v is None or not np.isfinite(v) else float(v)


class LiveEngine:
    def __init__(self, sensor, data: SensorData, cache_size: int = 2048) -> None:
        self.sensor = sensor
        self.data = data
        self.cache_size = cache_size
        self._cache: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT)

    # ------------------------------------------------------------------ windowing
    def window(self, bin_id: str, at: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame | None]:
        """Telemetry rows [start, at] where start covers the whole current cycle and the weather lags."""
        tel = self.data.bin_telemetry(bin_id)
        ts = tel["ts"]
        upto = tel[ts <= at]
        lifts = upto.loc[upto["lift_event"] == 1, "ts"]
        start = at - WEATHER_LOOKBACK
        if len(lifts):
            start = min(start, lifts.iloc[-1])
        # one more calendar day: the cycle's first day needs the previous day's RH (rh_mean_cycle)
        start = (start - pd.Timedelta(days=1)).normalize()
        rows = upto[upto["ts"] >= start]
        strokes = self.data.bin_strokes(bin_id)
        if strokes is not None:
            # strokes carry exact times; keep those inside the window's hours
            strokes = strokes[(strokes["ts"] >= start) & (strokes["ts"] < at + pd.Timedelta(hours=1))]
        return rows, strokes

    # ------------------------------------------------------------------ inference
    def estimate(self, bin_id: str, at: pd.Timestamp) -> dict:
        key = (bin_id, at.value)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return {**self._cache[key], "cached": True}
        if not self._slots.acquire(timeout=SLOT_WAIT_S):
            raise BusyError("all inference slots busy")
        try:
            result = self._run(bin_id, at)
        finally:
            self._slots.release()
        with self._lock:
            self._cache[key] = result
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return {**result, "cached": False}

    def _run(self, bin_id: str, at: pd.Timestamp) -> dict:
        from weight_train.features import build_features

        t0 = time.perf_counter()
        rows, strokes = self.window(bin_id, at)
        if rows.empty or rows["ts"].iloc[-1] != at:
            raise LookupError(f"no telemetry for {bin_id} at {at.isoformat()}")
        feats = build_features(rows, self.sensor.inverter, strokes)
        pred = self.sensor.predict_frame(feats).iloc[-1]
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        last = feats.X.iloc[-1]
        volume = float(rows["container_volume_m3"].iloc[-1])
        material = str(rows["material"].iloc[-1])
        setup = self.sensor.selection.get(material, "shared")
        return {
            "mass_kg": _num(pred["mass_pred_kg"]), "p10_kg": _num(pred["mass_p10_kg"]),
            "p90_kg": _num(pred["mass_p90_kg"]),
            "kg_m3": _num(pred["mass_pred_kg"] / volume) if volume else None,
            "inputs": {name: _num(float(last[name])) for name in DISPLAY_FEATURES if name in last.index},
            "window": {"start": rows["ts"].iloc[0].isoformat(), "rows": int(len(rows)),
                       "strokes": int(0 if strokes is None else len(strokes))},
            "model": {"setup": setup, "group": "all" if setup == "shared" else material,
                      "n_features": int(feats.X.shape[1])},
            "ms": round(elapsed_ms, 1),
        }


def score_batch(sensor, telemetry: pd.DataFrame, strokes: pd.DataFrame | None) -> pd.DataFrame:
    """Batch fallback: the same model over all loaded telemetry (the hourly job of a deployment)."""
    from weight_train.predict import predict_hourly
    return predict_hourly(sensor, telemetry, strokes)
