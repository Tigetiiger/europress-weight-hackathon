"""Metrics at lift time (the hour before lift_event) and per hour."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .modeling import MIN_LIFT_KG_FOR_PCT


def point_metrics(y: np.ndarray, pred: np.ndarray) -> dict:
    y, pred = np.asarray(y, float), np.asarray(pred, float)
    if len(y) == 0:
        return {"n": 0}
    err = pred - y
    ok = y >= MIN_LIFT_KG_FOR_PCT
    pct = np.abs(err[ok]) / y[ok] * 100
    return {
        "n": int(len(y)), "n_pct": int(ok.sum()),
        "mae_kg": float(np.mean(np.abs(err))),
        "mean_pct": float(pct.mean()) if ok.any() else float("nan"),
        "median_pct": float(np.median(pct)) if ok.any() else float("nan"),
        "within_5pct": float(np.mean(pct <= 5)) if ok.any() else float("nan"),
        "within_10pct": float(np.mean(pct <= 10)) if ok.any() else float("nan"),
        "bias_kg": float(err.mean()),
        "bias_pct": float(np.mean(err[ok] / y[ok]) * 100) if ok.any() else float("nan"),
    }


def interval_metrics(y: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> dict:
    if len(y) == 0:
        return {}
    return {"coverage": float(np.mean((y >= lo) & (y <= hi))), "width_kg": float(np.mean(hi - lo))}


def evaluate(frame: pd.DataFrame) -> dict:
    """frame: y_kg, pred_kg, [lo_kg, hi_kg], material, is_lift. Returns lift + hourly metrics."""
    out = {}
    for scope, sub in (("lift", frame[frame["is_lift"]]), ("hourly", frame)):
        res = {"overall": _metrics(sub)}
        res["by_material"] = {m: _metrics(g) for m, g in sub.groupby("material", observed=True)}
        out[scope] = res
    return out


def _metrics(sub: pd.DataFrame) -> dict:
    m = point_metrics(sub["y_kg"].to_numpy(), sub["pred_kg"].to_numpy())
    if "lo_kg" in sub:
        m.update(interval_metrics(sub["y_kg"].to_numpy(), sub["lo_kg"].to_numpy(), sub["hi_kg"].to_numpy()))
    return m


def breakdown(lifts: pd.DataFrame, column: str) -> dict:
    return {str(k): _metrics(g) for k, g in lifts.groupby(column, observed=True)}


def lift_breakdowns(lifts: pd.DataFrame) -> dict:
    """Error by infeed, drainage, class, size, false-full episode and wet/dry week (lift rows)."""
    d = lifts.copy()
    d["container_size"] = pd.cut(d["container_volume_m3"], [0, 14, 20, 100],
                                 labels=["<=14 m3", "15-20 m3", ">20 m3"])
    rain7 = d[[f"rain_mm_d{k}" for k in range(1, 8)]].sum(axis=1)
    lo, hi = rain7.quantile(0.25), rain7.quantile(0.75)
    d["week"] = np.where(rain7 >= hi, "wet (top 25 % rain7)", np.where(rain7 <= lo, "dry (bottom 25 %)", "middle"))
    d["false_full_in_cycle"] = d["false_full_in_cycle"].map({True: "yes", False: "no"})
    cols = ["infeed", "drainage", "equipment_class", "container_size", "false_full_in_cycle", "week"]
    return {c: breakdown(d, c) for c in cols}
