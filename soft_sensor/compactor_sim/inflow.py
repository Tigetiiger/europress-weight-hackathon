"""Material inflow: Poisson loading events, gamma event masses, press cycles.

Everything here is independent of container state, so it is vectorised over
(hour, bin) arrays of shape (T, N).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import DAY_TYPES, MATERIALS, SECTORS
from .fleet import material_index, sector_index
from .timeline import Timeline


@dataclass(frozen=True)
class Inflow:
    open_mask: np.ndarray    # (T, N) bool, site open
    events: np.ndarray       # (T, N) loading events
    mass_kg: np.ndarray      # (T, N) material mass at reference moisture
    presses: np.ndarray      # (T, N) ram strokes (loading-triggered + test cycles)


def _sector_tables(cfg: Mapping[str, Any]):
    """(S, 3) open/close hours (NaN = closed), (S, 24), (S, 7), (S, 12) factors, (S,) holiday flag."""
    n = len(SECTORS)
    start = np.full((n, len(DAY_TYPES)), np.nan)
    end = np.full_like(start, np.nan)
    for s, name in enumerate(SECTORS):
        for d, day_type in enumerate(DAY_TYPES):
            hours = cfg["sectors"][name]["open"][day_type]
            if hours is not None:
                start[s, d], end[s, d] = hours
    hour_w = np.array([cfg["sectors"][s]["hour_weights"] for s in SECTORS], dtype=float)
    day_f = np.array([cfg["sectors"][s]["day_factor"] for s in SECTORS], dtype=float)
    month_f = np.array([cfg["sectors"][s]["month_factor"] for s in SECTORS], dtype=float)
    closed_hol = np.array([cfg["sectors"][s]["closed_on_holidays"] for s in SECTORS])
    return start, end, hour_w, day_f, month_f, closed_hol


def opening_mask(cfg: Mapping[str, Any], tl: Timeline, fleet: pd.DataFrame) -> np.ndarray:
    start, end, _, _, _, closed_hol = _sector_tables(cfg)
    sec = sector_index(fleet)[None, :]
    day_type = np.where(tl.dow <= 4, 0, np.where(tl.dow == 5, 1, 2))[:, None]
    shift = fleet["open_shift_h"].to_numpy()[None, :]
    hour = tl.hour[:, None]
    with np.errstate(invalid="ignore"):
        is_open = (hour >= start[sec, day_type] + shift) & (hour < end[sec, day_type] + shift)
    holiday = tl.holiday[tl.day_idx][:, None] & closed_hol[sec]
    return is_open & ~holiday


def _daily_ar1_lognormal(n_days: int, n_bins: int, phi: float, sigma: float, rng) -> np.ndarray:
    x = np.empty((n_days, n_bins))
    x[0] = rng.standard_normal(n_bins)
    scale = np.sqrt(1 - phi**2)
    for d in range(1, n_days):
        x[d] = phi * x[d - 1] + scale * rng.standard_normal(n_bins)
    return np.exp(sigma * x - 0.5 * sigma**2)


def _bulk_days(open_mask, tl: Timeline, bcfg, rng) -> np.ndarray:
    open_day = np.add.reduceat(open_mask.astype(np.int32), tl.day_start, axis=0) > 0
    hit = open_day & (rng.random(open_day.shape) < bcfg["prob_per_open_day"])
    return np.where(hit, rng.uniform(*bcfg["multiplier"], open_day.shape), 1.0)


def event_rate(cfg, tl: Timeline, fleet: pd.DataFrame, open_mask, rng) -> np.ndarray:
    """(T, N) expected loading events per hour, scaled to each site's daily input."""
    _, _, hour_w, day_f, month_f, _ = _sector_tables(cfg)
    sec = sector_index(fleet)[None, :]
    shape = hour_w[sec, tl.hour[:, None]] * day_f[sec, tl.dow[:, None]] * month_f[sec, tl.month[:, None] - 1]
    shape = shape * open_mask
    per_day = shape[tl.main_start:].sum(axis=0) / tl.n_main_days
    mean_mass = np.array([cfg["inflow"]["event_mass_mean_kg"][m] for m in MATERIALS])[material_index(fleet)]
    events_per_day = fleet["daily_input_kg"].to_numpy() / mean_mass
    scale = np.divide(events_per_day, per_day, out=np.zeros_like(per_day), where=per_day > 0)
    icfg = cfg["inflow"]
    noise = _daily_ar1_lognormal(tl.n_days, len(fleet), icfg["daily_noise"]["ar1_phi"],
                                 icfg["daily_noise"]["sigma_log"], rng)
    bulk = _bulk_days(open_mask, tl, icfg["bulk_day"], rng)
    return shape * scale * (noise * bulk)[tl.day_idx]


def build_inflow(cfg: Mapping[str, Any], tl: Timeline, fleet: pd.DataFrame, rng: np.random.Generator) -> Inflow:
    icfg = cfg["inflow"]
    mask = opening_mask(cfg, tl, fleet)
    lam = event_rate(cfg, tl, fleet, mask, rng)
    events = rng.poisson(lam).astype(np.int32)
    k = icfg["event_mass_gamma_shape"]
    mean_mass = np.array([icfg["event_mass_mean_kg"][m] for m in MATERIALS])[material_index(fleet)]
    # sum of n iid Gamma(k, theta) draws is Gamma(n*k, theta)
    mass = rng.gamma(np.maximum(events * k, 1e-12), (mean_mass / k)[None, :])
    mass = np.where(events > 0, mass, 0.0)
    loading_presses = rng.binomial(events, icfg["press_prob_per_event"])
    tests = rng.poisson(icfg["test_cycle_rate_per_open_hour"] * mask)
    return Inflow(open_mask=mask, events=events, mass_kg=mass,
                  presses=(loading_presses + tests).astype(np.int32))
