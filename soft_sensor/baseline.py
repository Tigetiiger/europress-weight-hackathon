"""Baseline soft-sensor check: does pressure (and rain) beat fill-only at lift time?

    python baseline.py runs/<name>

For every lift, features are built only from telemetry available *before* the
lift (the cycle that ends at that lift) plus the last 7 days of weather.
LightGBM is trained on log(mass) with 5-fold cross-validation grouped by
machine, so a model cannot memorise a machine's hidden density factor.
Reported: mean absolute % error per lift, per feature set.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

HOURLY_COLS = ["bin_id", "ts", "fill_pct", "presses", "peak_pressure_bar", "temp_c", "lift_event"]
STATIC = ["material", "equipment_class", "container_volume_m3", "infeed", "drainage"]
CATEGORICAL = ["material", "equipment_class", "infeed"]
LAGS = range(1, 8)
LOG_OFFSET_KG = 50.0
MIN_MASS_FOR_PCT_KG = 200.0   # % error is meaningless for (near-)empty false-full lifts

FILL = ["fill_last", "fill_med6"]
PRESSURE = ["p_last", "p_max24", "p_med48", "p_n24", "temp_mean24"]
PRESSES = ["presses_cycle", "hours_cycle"]
WEATHER = [f"rain_d{k}" for k in LAGS] + [f"rh_d{k}" for k in LAGS]
FEATURE_SETS = {
    "fill_only": STATIC + FILL,
    "+pressure": STATIC + FILL + PRESSURE,
    "+pressure+presses": STATIC + FILL + PRESSURE + PRESSES,
    "+rain+rh (7 d)": STATIC + FILL + PRESSURE + PRESSES + WEATHER,
}

PARAMS = dict(objective="regression", learning_rate=0.03, n_estimators=800, num_leaves=31,
              min_child_samples=40, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
              reg_lambda=1.0, verbose=-1)


def cycle_features(hourly: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the telemetry of each (bin, cycle); cycle k ends with lift k+1."""
    h = hourly.sort_values(["bin_id", "ts"]).reset_index(drop=True)
    h["cycle"] = h.groupby("bin_id")["lift_event"].cumsum()
    keys = ["bin_id", "cycle"]
    g = h.groupby(keys, sort=False)
    out = pd.DataFrame({
        "presses_cycle": g["presses"].sum(),
        "hours_cycle": g["presses"].size(),
        "fill_last": g["fill_pct"].last(),          # last non-null reading
        "p_last": g["peak_pressure_bar"].last(),
    })
    tail6 = h.dropna(subset=["fill_pct"]).groupby(keys).tail(6)
    out["fill_med6"] = tail6.groupby(keys)["fill_pct"].median()
    t24 = h.groupby(keys).tail(24).groupby(keys)
    out["p_max24"] = t24["peak_pressure_bar"].max()
    out["p_n24"] = t24["peak_pressure_bar"].count()
    out["temp_mean24"] = t24["temp_c"].mean()
    out["p_med48"] = h.groupby(keys).tail(48).groupby(keys)["peak_pressure_bar"].median()
    return out.reset_index()


def weather_lags(lifts: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    w = weather.copy()
    w["date"] = pd.to_datetime(w["date"])
    w = w.set_index(["region", "date"]).sort_index()
    day = lifts["ts"].dt.tz_localize(None).dt.normalize()
    out = {}
    for k in LAGS:
        key = pd.MultiIndex.from_arrays([lifts["region"], day - pd.Timedelta(days=k)])
        out[f"rain_d{k}"] = w["precip_mm"].reindex(key).to_numpy()
        out[f"rh_d{k}"] = w["rh_pct"].reindex(key).to_numpy()
    return pd.DataFrame(out, index=lifts.index)


def build_dataset(run: Path) -> pd.DataFrame:
    hourly = pd.read_parquet(run / "hourly.parquet", columns=HOURLY_COLS)
    lifts = pd.read_parquet(run / "lifts.parquet")
    weather = pd.read_parquet(run / "weather_daily.parquet")
    lifts = lifts.sort_values(["bin_id", "ts"]).reset_index(drop=True)
    lifts["cycle"] = lifts.groupby("bin_id").cumcount()      # lift k closes cycle k
    data = lifts.merge(cycle_features(hourly), on=["bin_id", "cycle"], how="left")
    data = pd.concat([data, weather_lags(data, weather)], axis=1)
    data = data[data["cycle"] > 0]                            # first cycle starts before the reported period
    for col in CATEGORICAL:
        data[col] = data[col].astype("category")
    data["drainage"] = data["drainage"].astype(int)
    return data.reset_index(drop=True)


def group_folds(bins: pd.Series, n_folds: int, seed: int = 0) -> np.ndarray:
    uniq = bins.unique()
    rng = np.random.default_rng(seed)
    fold_of = dict(zip(uniq, rng.permutation(len(uniq)) % n_folds))
    return bins.map(fold_of).to_numpy()


def cross_val_predict(data: pd.DataFrame, features: list[str], folds: np.ndarray) -> np.ndarray:
    """Out-of-fold predicted lift mass (kg); trained on log(mass + 50 kg)."""
    y = np.log(data["true_mass_removed_kg"].to_numpy() + LOG_OFFSET_KG)
    pred = np.zeros(len(data))
    for f in np.unique(folds):
        tr, te = folds != f, folds == f
        model = lgb.LGBMRegressor(**PARAMS)
        model.fit(data.loc[tr, features], y[tr], categorical_feature=[c for c in CATEGORICAL if c in features])
        pred[te] = model.predict(data.loc[te, features])
    return np.maximum(np.exp(pred) - LOG_OFFSET_KG, 0.0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run", type=Path)
    ap.add_argument("--folds", type=int, default=5)
    args = ap.parse_args(argv)
    data = build_dataset(args.run)
    folds = group_folds(data["bin_id"], args.folds)
    print(f"{len(data)} lifts from {data['bin_id'].nunique()} machines, {args.folds}-fold CV grouped by machine\n")
    truth = data["true_mass_removed_kg"].to_numpy()
    ok = truth >= MIN_MASS_FOR_PCT_KG
    print(f"per-lift % error over the {ok.sum()} lifts >= {MIN_MASS_FOR_PCT_KG:.0f} kg; "
          f"WAPE (sum|err|/sum mass) over all lifts\n")
    results: dict = {}
    ape = {}
    for name, feats in FEATURE_SETS.items():
        pred = cross_val_predict(data, feats, folds)
        err = np.abs(pred - truth)
        ape[name] = np.where(ok, 100 * err / np.maximum(truth, 1.0), np.nan)
        results[name] = {"mean_ape_pct": float(np.nanmean(ape[name])), "median_ape_pct": float(np.nanmedian(ape[name])),
                         "p90_ape_pct": float(np.nanquantile(ape[name], 0.9)),
                         "wape_pct": float(100 * err.sum() / truth.sum()), "mae_kg": float(err.mean())}
    print(f"{'feature set':22s} {'mean %err':>9s} {'median':>7s} {'p90':>6s} {'WAPE':>6s} {'MAE kg':>7s}")
    for name in FEATURE_SETS:
        r = results[name]
        print(f"{name:22s} {r['mean_ape_pct']:9.2f} {r['median_ape_pct']:7.2f} {r['p90_ape_pct']:6.2f} "
              f"{r['wape_pct']:6.2f} {r['mae_kg']:7.0f}")
    segments = {
        **{f"material={m}": data["material"] == m for m in data["material"].cat.categories},
        "false-full lifts": data["false_full_trigger"],
        "cardboard open_hopper": (data["material"] == "cardboard") & (data["infeed"] == "open_hopper"),
        "cardboard indoor_shaft": (data["material"] == "cardboard") & (data["infeed"] == "indoor_shaft"),
    }
    print("\nmean %err by segment:")
    print(f"{'segment':26s} " + " ".join(f"{n:>18s}" for n in FEATURE_SETS))
    results["segments"] = {}
    for seg, mask in segments.items():
        mask = mask.to_numpy()
        print(f"{seg:26s} " + " ".join(f"{np.nanmean(ape[n][mask]):18.2f}" for n in FEATURE_SETS))
        results["segments"][seg] = {n: float(np.nanmean(ape[n][mask])) for n in FEATURE_SETS}
    (args.run / "baseline_results.json").write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
