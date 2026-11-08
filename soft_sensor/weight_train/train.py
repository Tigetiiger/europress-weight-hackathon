"""`train` command: run the splits, save bundles and all evaluation artifacts.

Artifacts under models/<run>/ (consumed by plots.py and report.py):
    metrics.json                 everything numeric (see README "Outputs")
    comparison.csv               ladder per split x step x material (lift metrics)
    learning_curve.csv           bins split: error vs number of training machines
    feature_list.json            feature sets, categorical features, features per selected model
    <split>/bundle.json + *.txt  the deployable soft sensor
    <split>/test_predictions.parquet   y_kg, pred_kg, lo_kg, hi_kg, is_lift, material, bin_id, ts, ...
    <split>/shap_<material>.parquet    shap__<feature> and value__<feature> columns
"""
from __future__ import annotations

import dataclasses
import json
import logging
import resource
import time
from pathlib import Path

import pandas as pd

from .bundle import model_key
from .experiment import load_training_data, run_split
from .features import CATEGORICAL, FEATURE_SETS
from .settings import TrainSettings

log = logging.getLogger(__name__)


def _peak_rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6


def _comparison_rows(kind: str, ladder: dict) -> list[dict]:
    rows = []
    for step, res in ladder.items():
        rows.append({"split": kind, "step": step, "material": "all", **res["overall"]})
        rows += [{"split": kind, "step": step, "material": m, **v} for m, v in res["by_material"].items()]
    return rows


def _curve_rows(curve: list[dict]) -> list[dict]:
    rows = []
    for point in curve:
        rows.append({"n_machines": point["n_machines"], "material": "all", **point["overall"]})
        rows += [{"n_machines": point["n_machines"], "material": m, **v} for m, v in point["by_material"].items()]
    return rows


def run_train(data_dir: Path, out: Path, splits: list[str], setup_mode: str, s: TrainSettings) -> dict:
    t0 = time.perf_counter()
    out.mkdir(parents=True, exist_ok=True)
    data = load_training_data(data_dir)
    t_feat = time.perf_counter() - t0
    log.info("features built in %.1f s (%d rows, %d columns)", t_feat, *data.base.X.shape)
    used_strokes = bool(data.base.X["stroke_peak_mean"].notna().any())
    results, comparison, curve, selected_features = {}, [], [], {}
    for kind in splits:
        res = run_split(data, kind, s, setup_mode)
        res.sensor.info["used_strokes"] = used_strokes
        res.sensor.save(out / kind)
        res.test_frame.to_parquet(out / kind / "test_predictions.parquet", index=False)
        for mat, frame in res.shap.items():
            frame.to_parquet(out / kind / f"shap_{mat}.parquet", index=False)
        results[kind] = res.metrics
        comparison += _comparison_rows(kind, res.metrics["ladder"])
        curve += _curve_rows(res.metrics.get("learning_curve", []))
        selected_features[kind] = {m: res.sensor.models[model_key(st, "all" if st == "shared" else m, "point")].features
                                   for m, st in res.sensor.selection.items()}
        log.info("[%s] done in %.0f s", kind, res.metrics["train_wall_s"])
    compute = {"train_wall_s": round(time.perf_counter() - t0, 1), "feature_build_s": round(t_feat, 1),
               "peak_rss_gb": round(_peak_rss_gb(), 2), "rows": int(len(data.base.X)),
               "machines": int(data.base.meta["bin_id"].nunique())}
    metrics = {"data": str(data_dir), "setup_mode": setup_mode, "settings": dataclasses.asdict(s),
               "compute": compute, "splits": results}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    pd.DataFrame(comparison).to_csv(out / "comparison.csv", index=False)
    pd.DataFrame(curve).to_csv(out / "learning_curve.csv", index=False)
    (out / "feature_list.json").write_text(json.dumps(
        {"feature_sets": FEATURE_SETS, "categorical": list(CATEGORICAL), "selected_model_features": selected_features},
        indent=2))
    log.info("training finished in %.0f s, peak RSS %.2f GB", compute["train_wall_s"], compute["peak_rss_gb"])
    return metrics
