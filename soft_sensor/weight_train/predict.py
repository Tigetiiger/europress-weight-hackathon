"""Load a trained soft sensor and predict hourly mass (works on data without true_ columns)."""
from __future__ import annotations

import json
import logging
import resource
import time
from pathlib import Path

import pandas as pd

from .bundle import SoftSensor
from .features import build_features

log = logging.getLogger(__name__)
OUTPUT_COLUMNS = ["bin_id", "ts", "material", "mass_pred_kg", "mass_p10_kg", "mass_p90_kg"]


def resolve_bundle(models_dir: Path, split: str | None) -> Path:
    if split:
        path = models_dir / split
    else:  # the pilot-fleet model if trained, else the new-installation model
        path = next((models_dir / k for k in ("time", "bins") if (models_dir / k / "bundle.json").is_file()), None)
    if path is None or not (path / "bundle.json").is_file():
        raise FileNotFoundError(f"no trained bundle under {models_dir} (split={split})")
    return path


def predict_hourly(sensor: SoftSensor, hourly: pd.DataFrame, strokes: pd.DataFrame | None = None) -> pd.DataFrame:
    if strokes is None and sensor.info.get("used_strokes"):
        log.warning("model was trained with per-stroke features but no strokes were given; they will be NaN")
    feats = build_features(hourly, sensor.inverter, strokes)
    return sensor.predict_frame(feats)[OUTPUT_COLUMNS]


def run_predict(models_dir: Path, input_path: Path, output_path: Path, split: str | None = None,
                strokes_path: Path | None = None) -> dict:
    t0 = time.perf_counter()
    sensor = SoftSensor.load(resolve_bundle(models_dir, split))
    hourly = pd.read_parquet(input_path)
    if strokes_path is None and (input_path.parent / "strokes.parquet").is_file():
        strokes_path = input_path.parent / "strokes.parquet"
    strokes = None
    if strokes_path is not None:
        strokes = pd.read_parquet(strokes_path, columns=["bin_id", "ts", "peak_pressure_bar", "duration_s"])
    t1 = time.perf_counter()
    out = predict_hourly(sensor, hourly, strokes)
    t_pred = time.perf_counter() - t1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(output_path, index=False)
    stats = {"rows": len(out), "wall_s": round(time.perf_counter() - t0, 2),
             "features_and_predict_s": round(t_pred, 2),
             "us_per_machine_hour": round(1e6 * t_pred / max(len(out), 1), 2),
             "peak_rss_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6, 2)}
    log.info("predicted %d machine-hours in %.1f s (%.1f us each), peak RSS %.2f GB -> %s",
             stats["rows"], t_pred, stats["us_per_machine_hour"], stats["peak_rss_gb"], output_path)
    try:  # recorded for REPORT.md; models_dir may be read-only in deployment
        (models_dir / "predict_stats.json").write_text(json.dumps(stats, indent=2))
    except OSError as exc:
        log.warning("could not write predict_stats.json: %s", exc)
    return stats
