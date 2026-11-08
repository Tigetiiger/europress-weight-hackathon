"""Normalised conformalised quantile regression (CQR), Mondrian by material x fill bucket.

score = max(q_lo - y, y - q_hi) / width, width = max(q_hi - q_lo, floor)
interval = [q_lo - Q * width, q_hi + Q * width],  Q = finite-sample (1 - alpha) quantile.
Strata with too few calibration points fall back to the material, then to the pool.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def width_floor(pred: np.ndarray) -> np.ndarray:
    return 0.02 * np.abs(pred) + 1.0   # kg/m3


def _quantile(scores: np.ndarray, coverage: float) -> float:
    n = len(scores)
    level = min(np.ceil((n + 1) * coverage) / n, 1.0)
    return float(np.quantile(scores, level, method="higher"))


@dataclass
class Conformal:
    buckets: tuple
    coverage: float
    q: dict = field(default_factory=dict)          # "material|bucket" -> Q
    q_material: dict = field(default_factory=dict)
    q_pool: float = 0.0

    def _bucket(self, fill: np.ndarray) -> np.ndarray:
        fill = np.nan_to_num(fill, nan=0.0)
        return np.clip(np.digitize(fill, self.buckets[1:-1]), 0, len(self.buckets) - 2)

    @classmethod
    def fit(cls, lo, hi, pred, y, material, fill, buckets, coverage, min_points,
            lift_rows: np.ndarray | None = None) -> "Conformal":
        """``lift_rows`` (optional): in the top fill bucket use the larger of the all-hours and
        the pre-lift-hours quantile, so the weighbridge comparison hour is covered too."""
        width = np.maximum(hi - lo, width_floor(pred))
        scores = np.maximum(lo - y, y - hi) / width
        out = cls(tuple(buckets), coverage)
        out.q_pool = _quantile(scores, coverage) if len(scores) else 0.0
        bucket = out._bucket(fill)
        for mat in np.unique(material):
            sel = material == mat
            if sel.sum() >= min_points:
                out.q_material[str(mat)] = _quantile(scores[sel], coverage)
            top = len(buckets) - 2
            for b in range(len(buckets) - 1):
                cell = sel & (bucket == b)
                if cell.sum() < min_points:
                    continue
                q = _quantile(scores[cell], coverage)
                lift_cell = cell & lift_rows if lift_rows is not None else None
                if b == top and lift_cell is not None and lift_cell.sum() >= min_points:
                    # cover both the pre-lift hour (weighbridge comparison) and ordinary hours
                    q = max(q, _quantile(scores[lift_cell], coverage))
                out.q[f"{mat}|{b}"] = q
        return out

    def apply(self, lo, hi, pred, material, fill) -> tuple[np.ndarray, np.ndarray]:
        width = np.maximum(hi - lo, width_floor(pred))
        f = self._factor_fast(np.asarray(material).astype(str), np.asarray(fill, dtype=float))
        return lo - f * width, hi + f * width

    def _factor_fast(self, material: np.ndarray, fill: np.ndarray) -> np.ndarray:
        bucket = self._bucket(fill)
        out = np.full(len(material), self.q_pool)
        for mat in np.unique(material):
            sel = material == mat
            out[sel] = self.q_material.get(mat, self.q_pool)
            for b in range(len(self.buckets) - 1):
                key = f"{mat}|{b}"
                if key in self.q:
                    out[sel & (bucket == b)] = self.q[key]
        return out

    def to_dict(self) -> dict:
        return {"buckets": list(self.buckets), "coverage": self.coverage, "q": self.q,
                "q_material": self.q_material, "q_pool": self.q_pool}

    @classmethod
    def from_dict(cls, d: dict) -> "Conformal":
        return cls(tuple(d["buckets"]), d["coverage"], dict(d["q"]), dict(d["q_material"]), float(d["q_pool"]))
