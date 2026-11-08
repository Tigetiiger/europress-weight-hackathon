"""Pressure-implied pack density.

Forward law (per material, fitted on training rows only):
    P / visc(T) - P_idle = k * (fill/100)^a * rho^b   (T = air temperature as the oil-viscosity proxy)
The training density is derived from the label (kg/m3 of container / fill), never
from simulator truth columns. Inverting gives rho_hat from an observed stroke.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .settings import (MATERIALS, MIN_FILL_FOR_PRESSURE, P_IDLE_NOMINAL_BAR, RELIEF_GUARD_BAR,
                       VISC_COEFF_PER_C, VISC_REF_C)

MIN_POINTS = 200
RHO_CLIP = (20.0, 900.0)


def viscosity(oil_c: np.ndarray) -> np.ndarray:
    return 1.0 + VISC_COEFF_PER_C * np.maximum(VISC_REF_C - oil_c, 0.0)


def usable(pressure: np.ndarray, fill: np.ndarray, oil_c: np.ndarray) -> np.ndarray:
    """Strokes that carry density information: real contact, below the relief valve."""
    with np.errstate(invalid="ignore"):
        excess = pressure / viscosity(oil_c) - P_IDLE_NOMINAL_BAR
        return (fill > MIN_FILL_FOR_PRESSURE) & (pressure < RELIEF_GUARD_BAR) & (excess > 5.0)


@dataclass(frozen=True)
class PressureInverter:
    coef: dict  # material -> (log_k, a, b)

    @classmethod
    def fit(cls, pressure, fill, oil_c, material, kg_m3) -> "PressureInverter":
        pressure, fill, oil_c, kg_m3 = (np.asarray(x, dtype=float) for x in (pressure, fill, oil_c, kg_m3))
        material = np.asarray(material).astype(str)
        rho = kg_m3 / np.maximum(fill / 100.0, 1e-3)
        ok = usable(pressure, fill, oil_c) & np.isfinite(rho) & (rho > RHO_CLIP[0]) & (rho < RHO_CLIP[1])
        coef = {}
        for mat in MATERIALS:
            sel = ok & (material == mat)
            if sel.sum() < MIN_POINTS:
                continue
            z = np.log(pressure[sel] / viscosity(oil_c[sel]) - P_IDLE_NOMINAL_BAR)
            design = np.column_stack([np.ones(sel.sum()), np.log(fill[sel] / 100.0), np.log(rho[sel])])
            sol, *_ = np.linalg.lstsq(design, z, rcond=None)
            if sol[2] > 0.1:                    # density must raise pressure, else not invertible
                coef[mat] = tuple(float(v) for v in sol)
        return cls(coef)

    def density(self, pressure, fill, oil_c, material) -> np.ndarray:
        pressure, fill, oil_c = (np.asarray(x, dtype=float) for x in (pressure, fill, oil_c))
        material = np.asarray(material).astype(str)
        out = np.full(len(pressure), np.nan)
        ok = usable(pressure, fill, oil_c)
        for mat, (log_k, a, b) in self.coef.items():
            sel = ok & (material == mat)
            z = np.log(pressure[sel] / viscosity(oil_c[sel]) - P_IDLE_NOMINAL_BAR)
            out[sel] = np.exp((z - log_k - a * np.log(fill[sel] / 100.0)) / b)
        return np.clip(out, *RHO_CLIP)

    def to_dict(self) -> dict:
        return {m: list(c) for m, c in self.coef.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "PressureInverter":
        return cls({m: tuple(c) for m, c in d.items()})


def empty_inverter() -> PressureInverter:
    return PressureInverter({})


__all__ = ["PressureInverter", "empty_inverter", "usable", "viscosity", "pd"]
