"""Static data for the soft-sensor pages (evaluation, weighbridge check, explainer).

    /home/hackathon/simulator-weather-and-stuff/.venv/bin/python pipeline/softsensor_data.py

Reads the simulator run + trained model artefacts (read-only) and writes
public/data/softsensor/{evaluation,check,explainer}.json and copies validation plots to
public/img/softsensor/. The live pages use the soft-sensor API instead (softsensor/).
ALL DATA IS SYNTHETIC.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("softsensor_data")
ROOT = Path(__file__).resolve().parents[1]
OUT_DATA = ROOT / "public" / "data" / "softsensor"
OUT_IMG = ROOT / "public" / "img" / "softsensor"
SPLITS = ("time", "bins")
MATERIALS = ("cardboard", "paper", "plastic_film", "mixed_waste")
LADDER = ("naive_fill_x_density", "fill_presses", "pressure", "pressure_graph", "full_shared",
          "full_per_material", "full_selected")
METRIC_KEYS = ("n", "mae_kg", "mean_pct", "median_pct", "within_5pct", "within_10pct", "bias_kg", "bias_pct",
               "coverage", "width_kg")
TOP_FEATURES = 15
CURVE_FILL_BANDS = ((15, 25), (35, 45), (55, 65), (75, 85), (90, 100))
CURVE_MACHINE = "BIN-0001"
SIM_PLOTS = ("pressure_graph.png", "pressure_vs_fill.png", "lifts.png", "temperature.png", "weather_effect.png")
MODEL_PLOTS = ("shap_time.png",)
NOTICE = ("Synthetic data from a physics-based simulator. Nothing here represents real Europress data "
          "or real Europress pressure settings.")


def _clean(v):
    if isinstance(v, (float, np.floating)):
        return None if not np.isfinite(v) else round(float(v), 4)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def _metrics(d: dict | None) -> dict | None:
    return None if d is None else {k: d.get(k) for k in METRIC_KEYS if k in d}


def _write(name: str, payload: dict) -> None:
    OUT_DATA.mkdir(parents=True, exist_ok=True)
    path = OUT_DATA / name
    path.write_text(json.dumps(_clean(payload), separators=(",", ":"), allow_nan=False))
    log.info("wrote %s (%.0f kB)", path.relative_to(ROOT), path.stat().st_size / 1024)


# ---------------------------------------------------------------- evaluation
def _importance(gain: dict) -> dict:
    out = {}
    for mat, feats in gain.items():
        total = sum(feats.values()) or 1.0
        top = sorted(feats.items(), key=lambda kv: -kv[1])[:TOP_FEATURES]
        out[mat] = [[name, value / total] for name, value in top]
    return out


def _shap(bundle_dir: Path) -> dict:
    """Mean |SHAP| per feature and material (kg/m3 units of the model target)."""
    out = {}
    for mat in MATERIALS:
        path = bundle_dir / f"shap_{mat}.parquet"
        if not path.is_file():
            continue
        shap = pd.read_parquet(path)
        cols = [c for c in shap.columns if c.startswith("shap__")]
        mean_abs = shap[cols].abs().mean().sort_values(ascending=False).head(TOP_FEATURES)
        out[mat] = [[c.removeprefix("shap__"), float(v)] for c, v in mean_abs.items()]
    return out


def evaluation(model_dir: Path) -> dict:
    metrics = json.loads((model_dir / "metrics.json").read_text())
    comparison = pd.read_csv(model_dir / "comparison.csv")
    splits = {}
    for split in SPLITS:
        s = metrics["splits"].get(split)
        if s is None:
            continue
        ladder = comparison[comparison["split"] == split]
        splits[split] = {
            "description": s["description"], "test_start": s["test_start"],
            "n_train_machines": s["n_train_machines"], "n_test_machines": s["n_test_machines"],
            "rows": s["rows"], "selection": s["selection"],
            "lift": {"overall": _metrics(s["final"]["lift"]["overall"]),
                     "by_material": {m: _metrics(v) for m, v in s["final"]["lift"]["by_material"].items()}},
            "hourly": {"overall": _metrics(s["final"]["hourly"]["overall"]),
                       "by_material": {m: _metrics(v) for m, v in s["final"]["hourly"]["by_material"].items()}},
            "ladder": {step: {r["material"]: {"mean_pct": r["mean_pct"], "within_5pct": r["within_5pct"]}
                              for _, r in ladder[ladder["step"] == step].iterrows()} for step in LADDER},
            "breakdowns": {k: {g: _metrics(v) for g, v in groups.items()} for k, groups in s["breakdowns"].items()},
            "importance": _importance(s["feature_importance_gain"]),
            "shap": _shap(model_dir / split),
        }
    curve = pd.read_csv(model_dir / "learning_curve.csv")
    learning = {m: [[int(r["n_machines"]), r["mean_pct"]] for _, r in curve[curve["material"] == m].iterrows()]
                for m in ("all", *MATERIALS)}
    predict_stats_path = model_dir / "predict_stats.json"
    predict_stats = json.loads(predict_stats_path.read_text()) if predict_stats_path.is_file() else {}
    return {"notice": NOTICE, "splits": splits, "learning_curve": learning,
            "learning_curve_split": "bins", "target_coverage": metrics["settings"]["target_coverage"],
            "compute": {**metrics["compute"], **{f"predict_{k}": v for k, v in predict_stats.items()}}}


# ---------------------------------------------------------------- weighbridge check
def check(run_dir: Path, model_dir: Path) -> dict:
    """Every test-period emptying: estimate 1 h before vs the weighbridge ticket (and simulator truth)."""
    lifts = pd.read_parquet(run_dir / "lifts.parquet",
                            columns=["bin_id", "ts", "trigger", "weighbridge_net_kg", "container_volume_m3"])
    lifts["bin_id"] = lifts["bin_id"].astype(str)
    out = {"notice": NOTICE, "splits": {}}
    for split in SPLITS:
        path = model_dir / split / "test_predictions.parquet"
        if not path.is_file():
            continue
        tp = pd.read_parquet(path, columns=["bin_id", "ts", "material", "is_lift", "y_kg", "pred_kg", "lo_kg",
                                            "hi_kg", "false_full_in_cycle"])
        tp = tp[tp["is_lift"] == 1].copy()
        tp["bin_id"] = tp["bin_id"].astype(str)
        tp["lift_ts"] = tp["ts"] + pd.Timedelta(hours=1)        # is_lift marks the hour before the lift
        joined = tp.merge(lifts.rename(columns={"ts": "lift_ts"}), on=["bin_id", "lift_ts"], how="inner")
        joined = joined.sort_values(["lift_ts", "bin_id"])
        if len(joined) != len(tp):
            log.warning("%s: %d of %d test lifts matched a weighbridge ticket", split, len(joined), len(tp))
        out["splits"][split] = {
            "n": int(len(joined)),
            "columns": ["bin_id", "t", "material", "volume_m3", "pred_kg", "p10_kg", "p90_kg", "weighbridge_kg",
                        "truth_kg", "trigger", "false_full"],
            "rows": [[r.bin_id, int(r.lift_ts.value // 1_000_000), r.material, float(r.container_volume_m3),
                      round(float(r.pred_kg), 1), round(float(r.lo_kg), 1), round(float(r.hi_kg), 1),
                      float(r.weighbridge_net_kg), round(float(r.y_kg), 1), r.trigger, bool(r.false_full_in_cycle)]
                     for r in joined.itertuples(index=False)],
        }
    return out


# ---------------------------------------------------------------- explainer
def pressure_curves(run_dir: Path) -> list:
    """One stroke's 16-point pressure graph per fill band, all from one machine."""
    curves = pd.read_parquet(run_dir / "pressure_curves.parquet", filters=[("bin_id", "==", CURVE_MACHINE)])
    hourly = pd.read_parquet(run_dir / "hourly.parquet", columns=["bin_id", "ts", "fill_pct"],
                             filters=[("bin_id", "==", CURVE_MACHINE)]).set_index("ts")
    curves["hour"] = curves["ts"].dt.floor("h")
    strokes = curves.groupby(["ts", "stroke_in_hour"], sort=True).agg(hour=("hour", "first")).reset_index()
    strokes["fill"] = hourly["fill_pct"].reindex(strokes["hour"]).to_numpy()
    out = []
    for lo, hi in CURVE_FILL_BANDS:
        pick = strokes[(strokes["fill"] >= lo) & (strokes["fill"] < hi)]
        if pick.empty:
            continue
        row = pick.iloc[len(pick) // 2]
        pts = curves[(curves["ts"] == row["ts"]) & (curves["stroke_in_hour"] == row["stroke_in_hour"])]
        pts = pts.sort_values("ram_position")
        out.append({"fill_pct": float(row["fill"]), "ts": row["ts"].isoformat(),
                    "position": [round(float(x), 3) for x in pts["ram_position"]],
                    "pressure_bar": [float(x) for x in pts["pressure_bar"]]})
    return out


def explainer(run_dir: Path, model_dir: Path) -> dict:
    summary = json.loads((run_dir / "summary.json").read_text())
    bins = pd.read_parquet(run_dir / "bins.parquet", columns=["material", "equipment_class", "region", "infeed"])
    bundle = json.loads((model_dir / "time" / "bundle.json").read_text())
    features = json.loads((model_dir / "feature_list.json").read_text())
    full = features.get("feature_sets", {}).get("full", [])
    return {
        "notice": NOTICE,
        "simulation": {"machines": summary["n_bins"], "days": summary["days"], "start": summary["start"],
                       "hourly_rows": summary["hourly_rows"], "lifts": summary["n_lifts"],
                       "false_full_share": summary["false_full_lift_share"],
                       "lift_mass_by_material": summary["lift_mass_by_material"],
                       "fleet": {c: bins[c].value_counts().to_dict() for c in bins.columns}},
        "model": {"n_features": len(full), "features": full, "selection": bundle.get("selection", {}),
                  "n_models": len(bundle.get("models", {}))},
        "pressure_curves": {"machine": CURVE_MACHINE, "curves": pressure_curves(run_dir)},
        "plots": [f"/img/softsensor/{p}" for p in (*SIM_PLOTS, *MODEL_PLOTS)],
    }


def copy_plots(run_dir: Path, model_dir: Path) -> None:
    OUT_IMG.mkdir(parents=True, exist_ok=True)
    for name in SIM_PLOTS:
        src = run_dir / "plots" / name
        if src.is_file():
            shutil.copy2(src, OUT_IMG / name)
    for name in MODEL_PLOTS:
        src = model_dir / "plots" / name
        if src.is_file():
            shutil.copy2(src, OUT_IMG / name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build static soft-sensor data for the dashboard")
    parser.add_argument("--sim-root", type=Path, default=ROOT.parent / "soft_sensor")
    parser.add_argument("--run", default="full400")
    parser.add_argument("--models", default="full400")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run_dir, model_dir = args.sim_root / "runs" / args.run, args.sim_root / "models" / args.models
    for path in (run_dir / "lifts.parquet", model_dir / "metrics.json"):
        if not path.is_file():
            log.error("missing %s", path)
            return 1
    _write("evaluation.json", evaluation(model_dir))
    _write("check.json", check(run_dir, model_dir))
    _write("explainer.json", explainer(run_dir, model_dir))
    copy_plots(run_dir, model_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
