"""Pure physical laws: compaction, ram pressure, oil viscosity and oil temperature."""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np


def compaction_gain(p_bar: np.ndarray, c: np.ndarray, p0: float, s_ref: np.ndarray) -> np.ndarray:
    """Density relative to rho_typ after a pack pressure ``p_bar`` (log-law).

    rho = rho0 * (1 + c ln(1 + P/P0)); normalised so the gain is 1 at P_ref.
    """
    return (1.0 + c * np.log1p(p_bar / p0)) / s_ref


def reference_gain(c: np.ndarray, p0: float, p_ref: float) -> np.ndarray:
    return 1.0 + c * math.log1p(p_ref / p0)


def fill_response(fill: np.ndarray, onset: float, exponent: float) -> np.ndarray:
    """f(fill): 0 while the ram barely touches the pack, 1 at a full container."""
    return np.clip((fill - onset) / (1.0 - onset), 0.0, 1.0) ** exponent


def pressure_demand(
    fill: np.ndarray,
    density_ratio: np.ndarray,
    moisture_extra: np.ndarray,
    frost_share: np.ndarray,
    par: Mapping[str, Any],
) -> np.ndarray:
    """Pack resistance in bar (above idle) before relief-valve capping.

    k * f(fill) * stiffness * (rho/rho_typ)^beta * wet softening * frost stiffening
    """
    soft = np.clip(1.0 - par["wet_softening"] * np.clip(moisture_extra, -0.1, 0.3), 0.3, 1.5)
    g = par["stiffness"] * np.maximum(density_ratio, 0.0) ** par["density_exponent"] * soft
    g = g * (1.0 + par["frost_stiffening"] * frost_share)
    return par["k"] * fill_response(fill, par["fill_onset"], par["fill_exponent"]) * g


def viscosity_factor(oil_c: np.ndarray, coeff_per_c: float, ref_c: float) -> np.ndarray:
    """Cold oil reads higher pressure: +coeff per degC below the reference."""
    return 1.0 + coeff_per_c * np.maximum(ref_c - oil_c, 0.0)


def oil_temperature(ambient: np.ndarray, presses: np.ndarray, ocfg: Mapping[str, Any]):
    """(T, N) oil temperature at the start and end of each hour.

    Newton cooling toward ambient plus a fixed warm-up per stroke.
    """
    decay = math.exp(-1.0 / ocfg["cool_time_constant_h"])
    start = np.empty_like(ambient)
    end = np.empty_like(ambient)
    temp = ambient[0].astype(np.float64)
    for t in range(len(ambient)):
        start[t] = temp
        temp = ambient[t] + (temp - ambient[t]) * decay + ocfg["heat_per_stroke_c"] * presses[t]
        temp = np.minimum(temp, ocfg["max_c"])
        end[t] = temp
    return start, end
