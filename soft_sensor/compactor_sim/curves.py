"""Pressure graph per stroke: hydraulic pressure along the ram travel (x = 0 start, 1 end).

    contact x_c = 1 - ingress - span * clip((fill - onset) / (1 - onset), 0, 1)   (+ noise from loose load)
    (the ram enters only ~20-30 cm into the container, so the rise is in the last part of the stroke)
    P(x) = min(relief, visc * (P_idle + excess * ramp(x)^q + loose(x)))
    ramp = clip((x - x_c) / (1 - x_c), 0, 1);  q = shape_base + shape_gain * (0.35 - c)
The end-of-stroke value equals the stroke's peak (strokes.py), so the curve is consistent
with peak_pressure_bar. The device derives per stroke:
    contact_pos   first x where the measured pressure rises > threshold above the start
    rise_slope    (P_end - P_contact) / (1 - contact_pos)      bar per stroke length
    mean_bar      mean measured pressure over the stroke (proportional to hydraulic work)
    completion    share of the travel done before the relief valve opened
"""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np

FEATURES = ("contact_pos", "rise_slope_bar", "mean_bar", "completion")
CHUNK = 500_000


def _chunk(ccfg, x, fill, c, idle, visc, excess, relief, gain, noise_sd, quantum, rng, spike):
    n = len(fill)
    onset, span = ccfg["contact_onset"], ccfg["contact_span"]
    xc = 1.0 - ccfg["ram_ingress_frac"] - span * np.clip((fill - onset) / (1.0 - onset), 0.0, 1.0)
    xc = np.clip(xc + ccfg["contact_noise"] * rng.standard_normal(n), 0.0, 1.0)[:, None]
    q = (ccfg["shape_base"] + ccfg["shape_gain"] * (0.35 - c))[:, None]
    ramp = np.clip((x - xc) / np.maximum(1.0 - xc, 1e-3), 0.0, 1.0) ** q
    loose = rng.uniform(0.0, ccfg["loose_bar_max"], (n, 1)) * np.minimum(x, xc)   # stays compressed
    bump = spike[:, None] * np.exp(-(((x - rng.uniform(0.75, 1.0, (n, 1))) / 0.08) ** 2))   # crushed item
    true = np.minimum(relief[:, None], visc[:, None] * (idle[:, None] + excess[:, None] * ramp + loose) + bump)
    meas = gain[:, None] * true + noise_sd * rng.standard_normal(true.shape)
    meas = np.round(meas / quantum) * quantum
    rise = meas - meas[:, :1] > ccfg["contact_threshold_bar"]
    k = x.size
    first = np.where(rise.any(axis=1), rise.argmax(axis=1), k - 1)
    contact = x[first]
    at_relief = true >= relief[:, None] * 0.999
    completion = np.where(at_relief.any(axis=1), x[at_relief.argmax(axis=1)], 1.0)
    slope = (meas[:, -1] - meas[np.arange(n), first]) / np.maximum(1.0 - contact, 1.0 / k)
    feats = np.column_stack([contact, slope, meas.mean(axis=1), completion]).astype(np.float32)
    return feats, meas.astype(np.float32)


def pressure_curves(cfg: Mapping[str, Any], fill: np.ndarray, c: np.ndarray, idle: np.ndarray,
                    visc: np.ndarray, excess: np.ndarray, relief: np.ndarray, gain: np.ndarray,
                    keep_curve: np.ndarray, rng: np.random.Generator, spike: np.ndarray | None = None):
    """Per-stroke curve features (S, 4) and the raw curves of strokes flagged in ``keep_curve``."""
    ccfg = cfg["hydraulics"]["curve"]
    ps = cfg["sensors"]["pressure"]
    x = np.linspace(0.0, 1.0, int(ccfg["n_points"]))
    feats = np.empty((len(fill), len(FEATURES)), dtype=np.float32)
    spike = np.zeros(len(fill)) if spike is None else spike
    kept = []
    for s in range(0, len(fill), CHUNK):
        sl = slice(s, s + CHUNK)
        f, meas = _chunk(ccfg, x, fill[sl], c[sl], idle[sl], visc[sl], excess[sl], relief[sl], gain[sl],
                         ps["noise_sd_bar"], ps["quantum_bar"], rng, spike[sl])
        feats[sl] = f
        kept.append(meas[keep_curve[sl]])
    curves = np.concatenate(kept) if kept else np.empty((0, x.size), dtype=np.float32)
    return feats, x, curves
