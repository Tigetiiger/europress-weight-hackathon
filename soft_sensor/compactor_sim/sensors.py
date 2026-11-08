"""Telemetry error model: fill sensor noise/bias/drift, false-full episodes, dropouts.

All random draws are made up front (vectorised); the hourly loop only combines
them with the true state via :meth:`SensorPlan.read_fill`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .timeline import Timeline


@dataclass(frozen=True)
class SensorPlan:
    fill_noise: np.ndarray    # (T, N) % points
    fill_gain: np.ndarray     # (T, N) multiplicative gain (bias + drift)
    dropout: np.ndarray       # (T, N) bool: no telemetry this hour
    ff_id: np.ndarray         # (T, N) false-full episode id, -1 = none
    ff_level: np.ndarray      # (T, N) reading level during an episode
    ff_noise_ratio: float     # false-full reading noise relative to fill noise
    ff_min_true_fill: float   # episodes only show once this much material is present
    round_to: float

    def read_fill(self, h: int, true_fill_pct: np.ndarray, cancelled: np.ndarray,
                  base_pct: np.ndarray | None = None):
        """(observed fill or NaN on dropout, false-full active, reading without spurious episode).

        ``base_pct`` is the firmware's pressure-derived estimate; without it a direct
        (ultrasonic) level sensor with gain bias/drift and noise is simulated.
        """
        if base_pct is None:
            reading = np.clip(true_fill_pct * self.fill_gain[h] + self.fill_noise[h], 0.0, 100.0)
        else:
            reading = np.clip(base_pct, 0.0, 100.0)
        unbridged = reading.copy()
        ep = self.ff_id[h]
        # bridging needs material piled in front of the sensor
        active = (ep >= 0) & (ep != cancelled) & (true_fill_pct >= self.ff_min_true_fill)
        bridged = self.ff_level[h] + self.fill_noise[h] * self.ff_noise_ratio
        reading = np.where(active, np.maximum(reading, bridged), reading)
        reading = np.clip(np.round(reading / self.round_to) * self.round_to, 0.0, 100.0)
        return np.where(self.dropout[h], np.nan, reading), active, unbridged


def _false_full(n_t: int, n_b: int, fcfg: Mapping[str, Any], rng: np.random.Generator):
    ff_id = np.full((n_t, n_b), -1, dtype=np.int32)
    ff_level = np.full((n_t, n_b), np.nan, dtype=np.float32)
    counts = rng.poisson(fcfg["rate_per_day"] * n_t / 24.0, n_b)
    episode = 0
    for b in range(n_b):
        for _ in range(counts[b]):
            start = int(rng.integers(n_t))
            stop = start + int(rng.uniform(*fcfg["duration_days"]) * 24)
            ff_id[start:stop, b] = episode
            ff_level[start:stop, b] = rng.uniform(*fcfg["level_pct"])
            episode += 1
    return ff_id, ff_level


def _dropouts(n_t: int, n_b: int, scfg: Mapping[str, Any], rng: np.random.Generator) -> np.ndarray:
    drop = rng.random((n_t, n_b)) < scfg["dropout_prob"]
    ocfg = scfg["outage"]
    counts = rng.poisson(ocfg["rate_per_day"] * n_t / 24.0, n_b)
    mu, sigma = ocfg["duration_h_lognormal"]
    for b in range(n_b):
        for _ in range(counts[b]):
            start = int(rng.integers(n_t))
            length = int(min(np.ceil(rng.lognormal(mu, sigma)), ocfg["max_h"]))
            drop[start:start + length, b] = True
    return drop


def build_sensor_plan(cfg: Mapping[str, Any], tl: Timeline, fleet: pd.DataFrame,
                      rng: np.random.Generator) -> SensorPlan:
    scfg = cfg["sensors"]
    fcfg = scfg["fill"]
    n_t, n_b = tl.n_hours, len(fleet)
    drift = np.cumsum(fcfg["drift_sd_per_day"] * rng.standard_normal((tl.n_days, n_b)), axis=0)
    gain = 1.0 + fleet["fill_gain_bias"].to_numpy()[None, :] + drift[tl.day_idx]
    ff_id, ff_level = _false_full(n_t, n_b, scfg["false_full"], rng)
    return SensorPlan(
        fill_noise=(fcfg["noise_sd_pct"] * rng.standard_normal((n_t, n_b))).astype(np.float32),
        fill_gain=gain.astype(np.float32),
        dropout=_dropouts(n_t, n_b, scfg, rng),
        ff_id=ff_id,
        ff_level=ff_level,
        ff_noise_ratio=float(scfg["false_full"]["noise_sd_pct"]) / float(fcfg["noise_sd_pct"]),
        ff_min_true_fill=float(scfg["false_full"]["min_true_fill_pct"]),
        round_to=float(fcfg["round_to_pct"]),
    )


class PressureFillEstimator:
    """Firmware fill estimate: rolling average of the strokes' back pressure, mapped linearly to fill.

    fill = 100 * clip((P_avg / visc - P_idle_nom) / (P_full * stiffness), 0, 1)
    P_avg is an exponential rolling average over ~``rolling_strokes`` strokes (alpha = 2/(n+1)
    per stroke), which suppresses spikes such as a box crushed on the way in. The firmware
    "reads full" at a set back pressure. With ``calibration: per_installation`` that setpoint is
    tuned at commissioning (machine scale, idle, typical density); day-to-day density, spikes,
    cold oil and noise remain, so it measures volume to ~5 %. Reset at a lift.
    """

    def __init__(self, cfg: Mapping[str, Any], fleet: pd.DataFrame, rng: np.random.Generator, n_hours: int):
        pf, hyd = cfg["sensors"]["pressure_fill"], cfg["hydraulics"]
        mats = fleet["material"].to_numpy()
        self.full = pf["full_backpressure_bar"] * np.array([hyd["stiffness"][m] for m in mats])
        self.idle = np.full(len(fleet), float(pf["p_idle_nominal_bar"]))
        if pf.get("calibration", "nominal") == "per_installation":
            # commissioning: the setpoint absorbs the machine's hydraulic scale, idle and typical density
            self.full = self.full * fleet["k_machine"].to_numpy() * (
                fleet["density_factor"].to_numpy() ** hyd["density_exponent"])
            self.idle = fleet["p_idle_bar"].to_numpy().astype(float)
        self.alpha = 2.0 / (pf["rolling_strokes"] + 1.0)
        self.visc_coeff = hyd["viscosity_coeff_per_c"] if pf["compensate_oil_temp"] else 0.0
        self.visc_ref = hyd["viscosity_ref_c"]
        self.spike_p = np.array([hyd["spikes"]["prob_per_stroke"][m] for m in mats])
        self.spike_bar = tuple(hyd["spikes"]["bar"])
        self.noise_sd = cfg["sensors"]["pressure"]["noise_sd_bar"]
        self.rng = rng
        self.avg = np.full(len(fleet), np.nan)

    def reset(self, idx: np.ndarray) -> None:
        self.avg[idx] = np.nan

    def update(self, h: int, presses: np.ndarray, p_true: np.ndarray, gain: np.ndarray,
               oil_c: np.ndarray, relief: np.ndarray) -> np.ndarray:
        """Fold this hour's strokes into the rolling average; return the fill estimate (%)."""
        n = presses.astype(float)
        pressed = n > 0
        spikes = self.rng.binomial(presses, self.spike_p) * self.rng.uniform(*self.spike_bar, len(n))
        hour_mean = np.minimum(relief, np.nan_to_num(p_true) + spikes / np.maximum(n, 1.0))
        read = gain * hour_mean + self.noise_sd / np.sqrt(np.maximum(n, 1.0)) * self.rng.standard_normal(len(n))
        visc = 1.0 + self.visc_coeff * np.maximum(self.visc_ref - oil_c, 0.0)
        excess = read / visc - self.idle
        w = 1.0 - (1.0 - self.alpha) ** n
        start = pressed & np.isnan(self.avg)
        self.avg = np.where(start, excess, np.where(pressed, self.avg + w * (excess - self.avg), self.avg))
        return 100.0 * np.clip(np.nan_to_num(self.avg) / self.full, 0.0, 1.0)


def pressure_gain(fleet: pd.DataFrame, years: np.ndarray, bin_idx: np.ndarray) -> np.ndarray:
    """Transducer gain for strokes at ``years`` since start on bins ``bin_idx``."""
    bias = fleet["pressure_gain_bias"].to_numpy()[bin_idx]
    drift = fleet["pressure_gain_drift_per_year"].to_numpy()[bin_idx]
    return 1.0 + bias + drift * years
