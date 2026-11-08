"""Tests for the soft-sensor training pipeline on a tiny synthetic run."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from compactor_sim.config import load_config
from compactor_sim.output import write_run
from compactor_sim.simulate import run_simulation
from weight_train.columns import LeakageError, assert_no_leakage
from weight_train.features import FEATURE_SETS, base_features, build_features
from weight_train.modeling import Rows, fit
from weight_train.physics import PressureInverter, empty_inverter
from weight_train.predict import OUTPUT_COLUMNS, run_predict
from weight_train.settings import TrainSettings
from weight_train.train import run_train

FAST = TrainSettings(max_rows_per_model=20_000, max_rounds=60, early_stopping_rounds=10, fixed_rounds=60,
                     grid=({"num_leaves": 15, "min_data_in_leaf": 30},), learning_curve_sizes=(4,),
                     shap_rows=100, num_threads=4, min_cal_points=10)


@pytest.fixture(scope="module")
def tiny_run(tmp_path_factory) -> Path:
    cfg = load_config(n_bins=12, days=70, seed=11)
    cfg["run"]["burn_in_days"] = 10
    out = tmp_path_factory.mktemp("sim") / "run"
    write_run(run_simulation(cfg), out)
    return out


@pytest.fixture(scope="module")
def hourly(tiny_run) -> pd.DataFrame:
    return pd.read_parquet(tiny_run / "hourly.parquet")


@pytest.fixture(scope="module")
def trained(tiny_run, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("models") / "m"
    run_train(tiny_run, out, ["bins", "time"], "both", FAST)
    return out


# ----------------------------------------------------------------------------- leakage
def test_no_true_column_reaches_the_feature_matrix(hourly):
    assert any(c.startswith("true_") for c in hourly.columns)          # the input does contain truth
    X = build_features(hourly, empty_inverter()).X
    assert not [c for c in X.columns if c.startswith("true_")]
    for col in ("lifted_mass_kg", "side_waste_kg", "ref_scale_kg", "density_factor"):
        assert col not in X.columns


def test_leakage_guard_rejects_planted_truth_columns(hourly):
    with pytest.raises(LeakageError):
        assert_no_leakage(["fill_ffill", "true_mass_kg"])
    with pytest.raises(LeakageError):
        assert_no_leakage(["fill_ffill", "density_factor"])
    feats = base_features(hourly)
    X = feats.X.assign(true_mass_kg=1.0)
    rows = Rows(np.arange(100), np.ones(100), np.ones(100))
    with pytest.raises(LeakageError):
        fit(X, rows, None, ["fill_ffill", "true_mass_kg"], FAST, FAST.grid[0], rounds=2)


def test_truth_columns_do_not_change_features(hourly):
    a = build_features(hourly, empty_inverter()).X
    scrambled = hourly.assign(**{c: np.random.default_rng(0).permutation(hourly[c].to_numpy())
                                 for c in hourly.columns if c.startswith("true_")})
    b = build_features(scrambled, empty_inverter()).X
    pd.testing.assert_frame_equal(a, b)


# ----------------------------------------------------------------------------- causality
def test_features_use_only_past_rows(hourly):
    """Cut every machine at hour t and scramble everything after: features <= t must not change."""
    inverter = PressureInverter({"cardboard": (-2.5, 2.5, 1.45), "paper": (-3.0, 2.6, 1.4),
                                 "plastic_film": (-2.7, 2.2, 1.5), "mixed_waste": (-2.6, 2.2, 1.6)})
    ts = np.sort(hourly["ts"].unique())
    cut = ts[len(ts) * 2 // 3] + pd.Timedelta(minutes=30)   # mid-day cut
    full = build_features(hourly, inverter)
    future = hourly["ts"] > cut
    changed = hourly.copy()
    rng = np.random.default_rng(1)
    for col in ("fill_pct", "presses", "peak_pressure_bar", "temp_c", "precip_mm", "oil_temp_c", "lift_event"):
        changed.loc[future, col] = rng.permutation(changed.loc[future, col].to_numpy())
    truncated = build_features(hourly[~future], inverter)
    perturbed = build_features(changed, inverter)
    keep = (full.meta["ts"] <= cut).to_numpy()
    pd.testing.assert_frame_equal(full.X[keep].reset_index(drop=True), truncated.X.reset_index(drop=True))
    pd.testing.assert_frame_equal(full.X[keep].reset_index(drop=True),
                                  perturbed.X[keep].reset_index(drop=True))


def test_daily_weather_lags_use_completed_days_only(hourly):
    X = build_features(hourly, empty_inverter()).X
    b = hourly.sort_values(["bin_id", "ts"], kind="stable").reset_index(drop=True)
    day = b["ts"].dt.tz_localize(None).dt.normalize()
    one = (b["bin_id"] == b["bin_id"].iloc[0]).to_numpy()
    daily = b[one].groupby(day[one])["precip_mm_day"].first()
    row = np.flatnonzero(one)[24 * 20 + 5]
    expected = daily.loc[day.iloc[row] - pd.Timedelta(days=1)]
    assert X["rain_mm_d1"].iloc[row] == pytest.approx(expected)
    assert "precip_mm_day" not in X.columns and "rh_pct" not in X.columns


# ----------------------------------------------------------------------------- end to end
def test_predict_output_schema_without_true_columns(trained, hourly, tmp_path):
    device_only = hourly[[c for c in hourly.columns if not c.startswith("true_")]]
    src = tmp_path / "hourly.parquet"
    device_only.to_parquet(src, index=False)
    out = tmp_path / "pred.parquet"
    stats = run_predict(trained, src, out)
    pred = pd.read_parquet(out)
    assert list(pred.columns) == OUTPUT_COLUMNS
    assert len(pred) == len(hourly)
    assert str(pred["ts"].dt.tz) == "Europe/Helsinki"
    for col in ("mass_pred_kg", "mass_p10_kg", "mass_p90_kg"):
        assert pred[col].dtype.kind == "f" and pred[col].notna().all()
    assert (pred["mass_p10_kg"] <= pred["mass_pred_kg"] + 1e-6).all()
    assert (pred["mass_pred_kg"] <= pred["mass_p90_kg"] + 1e-6).all()
    assert (pred["mass_p10_kg"] >= 0).all()
    assert stats["us_per_machine_hour"] > 0


def test_metrics_on_tiny_run_are_complete_and_sane(trained):
    m = json.loads((trained / "metrics.json").read_text())
    for split in ("bins", "time"):
        res = m["splits"][split]
        lift = res["final"]["lift"]["overall"]
        assert lift["n"] > 0 and np.isfinite(lift["mae_kg"]) and 0 <= lift["coverage"] <= 1
        assert set(res["ladder"]) >= {"naive_fill_x_density", "fill_presses", "pressure", "full_shared"}
        assert np.isfinite(res["final"]["hourly"]["overall"]["mae_kg"])
        assert (trained / split / "bundle.json").is_file()
    assert m["splits"]["bins"]["learning_curve"]
    for name in ("comparison.csv", "learning_curve.csv", "feature_list.json"):
        assert (trained / name).is_file()
    features = json.loads((trained / "feature_list.json").read_text())
    for kind in features["selected_model_features"].values():
        for cols in kind.values():
            assert_no_leakage(cols)
    assert set(FEATURE_SETS["full"]) >= set(FEATURE_SETS["pressure"])


def test_same_data_and_seed_give_same_metrics(tiny_run, trained, tmp_path):
    again = tmp_path / "again"
    run_train(tiny_run, again, ["time"], "both", FAST)
    a = json.loads((trained / "metrics.json").read_text())["splits"]["time"]["final"]
    b = json.loads((again / "metrics.json").read_text())["splits"]["time"]["final"]
    assert a == b


def test_oil_temperature_is_not_a_model_input(hourly):
    """No oil-temperature sensor is assumed: the column may be present but must change nothing."""
    inverter = PressureInverter({"cardboard": (-2.5, 2.5, 1.45), "paper": (-3.0, 2.6, 1.4),
                                 "plastic_film": (-2.7, 2.2, 1.5), "mixed_waste": (-2.6, 2.2, 1.6)})
    a = build_features(hourly, inverter).X
    assert not [c for c in a.columns if "oil" in c]
    scrambled = hourly.assign(oil_temp_c=np.random.default_rng(3).uniform(-30, 80, len(hourly)))
    pd.testing.assert_frame_equal(a, build_features(scrambled, inverter).X)
    pd.testing.assert_frame_equal(a, build_features(hourly.drop(columns=["oil_temp_c"]), inverter).X)
