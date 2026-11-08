"""Per-stroke hydraulics, computed after the hourly loop (fully vectorised).

For each stroke k (0-based) in an hour:
    oil_k  = oil at hour start + k * heat per stroke   (first strokes are cold)
    true_k = min(relief, visc(oil_k) * (P_idle + demand * resistance_noise))
    read_k = gain(t) * true_k + N(0, 4 bar), quantised to 0.5 bar
The hourly peak_pressure_bar is the max reading of the hour.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .curves import FEATURES as CURVE_FEATURES, pressure_curves
from .physics import viscosity_factor
from .sensors import pressure_gain
from .timeline import Timeline

HOURS_PER_YEAR = 365.25 * 24


@dataclass(frozen=True)
class StrokeResult:
    peak_bar: np.ndarray       # (N*Tm,) bin-major, NaN if no stroke or dropout
    energy_kwh: np.ndarray     # (N*Tm,) bin-major
    table: pd.DataFrame | None
    curve_hourly: dict         # feature -> (N*Tm,) hourly mean of per-stroke pressure-graph features
    curves_sample: pd.DataFrame | None   # raw pressure graphs of a few machines (long format)


def _bin_major(a: np.ndarray) -> np.ndarray:
    """(Tm, N) -> (N*Tm,) with all hours of bin 0 first."""
    return np.ascontiguousarray(a.T).ravel()


def _stroke_physics(cfg, fleet, b, oil, demand, years, rng):
    hyd = cfg["hydraulics"]
    st = hyd["stroke"]
    sigma = np.sqrt(np.log1p(hyd["stroke_resistance_cv"] ** 2))
    resistance = rng.lognormal(-0.5 * sigma**2, sigma, len(b))
    idle = fleet["p_idle_bar"].to_numpy()[b]
    relief = fleet["relief_bar"].to_numpy()[b]
    visc = viscosity_factor(oil, hyd["viscosity_coeff_per_c"], hyd["viscosity_ref_c"])
    excess = demand * resistance
    p_spike = np.array([hyd["spikes"]["prob_per_stroke"][m] for m in fleet["material"]])[b]
    spike = np.where(rng.random(len(b)) < p_spike, rng.uniform(*hyd["spikes"]["bar"], len(b)), 0.0)
    true = np.minimum(relief, visc * (idle + excess) + spike)
    ps = cfg["sensors"]["pressure"]
    gain = pressure_gain(fleet, years, b)
    noisy = gain * true + ps["noise_sd_bar"] * rng.standard_normal(len(b))
    reading = np.round(noisy / ps["quantum_bar"]) * ps["quantum_bar"]
    load = np.clip((true / visc - idle) / (relief - idle), 0.0, 1.0)
    dsig = np.sqrt(np.log1p(st["duration_cv"] ** 2))
    duration = (fleet["cycle_s"].to_numpy()[b] * (1 + st["duration_load_gain"] * load**2)
                * (1 + st["duration_cold_per_c"] * np.maximum(st["duration_cold_ref_c"] - oil, 0.0))
                * rng.lognormal(-0.5 * dsig**2, dsig, len(b)))
    energy = (fleet["motor_kw"].to_numpy()[b] * duration / 3600.0
              * (st["energy_base_load"] + st["energy_load_gain"] * load) / st["motor_efficiency"])
    parts = {"idle": idle, "relief": relief, "visc": visc, "excess": excess, "gain": gain, "spike": spike}
    return true, reading, duration, energy, parts


def build_strokes(cfg: Mapping[str, Any], tl: Timeline, fleet: pd.DataFrame, presses: np.ndarray,
                  demand: np.ndarray, oil_start: np.ndarray, dropout: np.ndarray,
                  rng: np.random.Generator, keep_table: bool, fill_true: np.ndarray,
                  compressibility: np.ndarray) -> StrokeResult:
    """``presses``/``oil_start``/``dropout`` are full-span (T, N); ``demand``/``fill_true`` (%) are (Tm, N)."""
    main = tl.main_start
    n_hours = tl.n_hours - main
    count = _bin_major(presses[main:])
    rows = np.flatnonzero(count > 0)
    per_row = count[rows]
    first = np.cumsum(per_row) - per_row
    row_of = np.repeat(rows, per_row)
    k = np.arange(per_row.sum()) - np.repeat(first, per_row)
    b, t = row_of // n_hours, row_of % n_hours
    oil = _bin_major(oil_start[main:])[row_of] + cfg["hydraulics"]["oil"]["heat_per_stroke_c"] * k
    true, reading, duration, energy, parts = _stroke_physics(
        cfg, fleet, b, oil, _bin_major(demand)[row_of], t / HOURS_PER_YEAR, rng)
    keep_curve = b < int(cfg["hydraulics"]["curve"]["sample_bins"])
    curve_feats, x, curves = pressure_curves(
        cfg, _bin_major(fill_true)[row_of] / 100.0, compressibility[b], parts["idle"], parts["visc"],
        parts["excess"], parts["relief"], parts["gain"], keep_curve, rng, parts["spike"])

    peak = np.full(len(count), np.nan, dtype=np.float32)
    energy_h = np.zeros(len(count), dtype=np.float32)
    if rows.size:
        peak[rows] = np.maximum.reduceat(reading, first)
        energy_h[rows] = np.add.reduceat(energy, first)
    drop = _bin_major(dropout[main:])
    peak[drop] = np.nan
    curve_hourly = {}
    for j, name in enumerate(CURVE_FEATURES):
        col = np.full(len(count), np.nan, dtype=np.float32)
        if rows.size:
            col[rows] = np.add.reduceat(curve_feats[:, j].astype(np.float64), first) / per_row
        col[drop] = np.nan
        curve_hourly[f"curve_{name}"] = col
    ts_stroke = None

    table = None
    if keep_table:
        offset_s = (k + rng.random(len(k))) / np.repeat(per_row, per_row) * 3600.0
        ts_ns = tl.ts.asi8[main:][t] + (offset_s * 1e9).astype(np.int64)
        ts_stroke = pd.to_datetime(ts_ns, utc=True).tz_convert(cfg["run"]["timezone"])
        table = pd.DataFrame({
            "bin_id": pd.Categorical.from_codes(b, fleet["bin_id"].to_numpy()),
            "ts": pd.to_datetime(ts_ns, utc=True).tz_convert(cfg["run"]["timezone"]),
            "stroke_in_hour": k.astype(np.int16),
            "oil_temp_c": oil.astype(np.float32),
            "true_peak_bar": true.astype(np.float32),
            "peak_pressure_bar": reading.astype(np.float32),
            "duration_s": duration.astype(np.float32),
            "energy_kwh": energy.astype(np.float32),
            **{f"curve_{n}": curve_feats[:, j] for j, n in enumerate(CURVE_FEATURES)},
        })
    sample = None
    if keep_curve.any():
        kb, kk = b[keep_curve], k[keep_curve]
        kts = (ts_stroke[keep_curve] if ts_stroke is not None
               else pd.to_datetime(tl.ts.asi8[main:][t[keep_curve]], utc=True).tz_convert(cfg["run"]["timezone"]))
        npts = x.size
        sample = pd.DataFrame({
            "bin_id": np.repeat(fleet["bin_id"].to_numpy()[kb], npts),
            "ts": np.repeat(kts, npts),
            "stroke_in_hour": np.repeat(kk, npts).astype(np.int16),
            "ram_position": np.tile(x, len(kb)).astype(np.float32),
            "pressure_bar": curves.ravel(),
        })
    return StrokeResult(peak_bar=peak, energy_kwh=energy_h, table=table, curve_hourly=curve_hourly,
                        curves_sample=sample)
