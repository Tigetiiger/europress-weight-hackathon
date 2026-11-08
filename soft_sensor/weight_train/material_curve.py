"""How does one material's soft sensor improve with the number of training machines?

    python -m weight_train curve --data runs/full400 --models models/full400 --material cardboard

Trains a per-material model (same hyper-parameters, rounds, monotone setting and features as the
tuned per-material model of the `bins` bundle) on n = 1, 2, 4, 8, ... machines of that material.
Everything is fitted on those n machines only, including the pressure inversion. Two tests:
    unseen : a fixed set of held-out machines of that material (the `bins` split test machines),
             last 25 % of the period - identical for every n, so the curve is comparable
    same   : the n training machines themselves in the last 25 % of the period
Small n are repeated with different machine draws; the mean and the min-max range are reported.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import multiprocessing as mp
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .experiment import load_training_data  # noqa: E402
from .features import Features, add_pressure_density  # noqa: E402
from .metrics import point_metrics  # noqa: E402
from .modeling import fit, make_rows  # noqa: E402
from .physics import PressureInverter  # noqa: E402
from .settings import TrainSettings  # noqa: E402
from .splits import make_split  # noqa: E402

log = logging.getLogger(__name__)
DEFAULT_SIZES = (1, 2, 3, 4, 6, 8, 10, 12, 16, 24, 32, 48, 64, 96, 128)
TARGET_PCT = 5.0
THREADS_PER_FIT = 4
_SHARED: dict = {}          # filled before forking; workers read it copy-on-write


def _worker(task: tuple) -> tuple:
    n, r, chosen = task
    sh = _SHARED
    rng = np.random.default_rng(sh["s"].seed + 104729 * r + n)
    out = _one_run(sh["feats"], sh["y"], sh["pre"], chosen, sh["masks"], sh["ref"], sh["s"], rng)
    return n, r, out


def repeats_for(n: int, pool: int) -> int:
    if n >= pool:
        return 1
    return 5 if n <= 8 else 3 if n <= 32 else 1


def _reference_model(models_dir: Path, material: str) -> dict:
    bundle = json.loads((models_dir / "bins" / "bundle.json").read_text())
    key = f"per_material__{material}__point"
    if key not in bundle["models"]:
        raise ValueError(f"{key} not in {models_dir}/bins/bundle.json - train with --setup both first")
    return bundle["models"][key]


def _subset(data, material: str) -> tuple[Features, np.ndarray, np.ndarray]:
    sel = (data.base.meta["material"] == material).to_numpy()
    feats = Features(X=data.base.X[sel].reset_index(drop=True), meta=data.base.meta[sel].reset_index(drop=True))
    return feats, data.y_kg[sel], data.pre_lift[sel]


def _one_run(feats, y, pre, chosen, masks, ref, s, rng) -> dict:
    meta = feats.meta
    in_chosen = meta["bin_id"].isin(chosen).to_numpy()
    train = in_chosen & masks["before"] & masks["complete"]
    vol = meta["container_volume_m3"].to_numpy()
    X0 = feats.X
    inv = PressureInverter.fit(X0["p_now"][train], X0["fill_ffill"][train], X0["temp_c"][train],
                               meta["material"][train], (y / vol)[train])
    X = add_pressure_density(feats, inv).X
    rows = make_rows(train, pre, y / vol, s, rng)
    model = fit(X, rows, None, ref["features"], s, ref["params"], monotone=ref["monotone"], rounds=ref["best_iter"])
    out = {}
    for name, mask in (("unseen", masks["unseen"]), ("same", in_chosen & masks["after"] & masks["complete"] & pre)):
        idx = np.flatnonzero(mask)
        out[name] = point_metrics(y[idx], model.predict(X.iloc[idx]) * vol[idx]) if idx.size else {"n": 0}
    return out


def _summarise(runs: list[dict], test: str) -> dict:
    vals = {k: [r[test].get(k, np.nan) for r in runs] for k in ("mean_pct", "median_pct", "within_5pct", "n")}
    pct = np.array(vals["mean_pct"], dtype=float)
    return {"mean_pct": float(np.nanmean(pct)), "min_pct": float(np.nanmin(pct)), "max_pct": float(np.nanmax(pct)),
            "median_pct": float(np.nanmean(vals["median_pct"])), "within_5pct": float(np.nanmean(vals["within_5pct"])),
            "n_lifts": float(np.nanmean(vals["n"]))}


def run_material_curve(data_dir: Path, models_dir: Path, material: str, sizes=DEFAULT_SIZES,
                       s: TrainSettings | None = None) -> dict:
    s = s or TrainSettings()
    t0 = time.perf_counter()
    data = load_training_data(data_dir, material=material)
    split = make_split(data.base.meta, "bins", s)
    ref = _reference_model(models_dir, material)
    feats, y, pre = _subset(data, material)
    meta = feats.meta
    test_bins = sorted(b for b in split.test_bins if b in set(meta["bin_id"]))
    pool = np.array(sorted(b for b in split.train_bins if b in set(meta["bin_id"])))
    masks = {"before": (meta["ts"] < split.t_test).to_numpy(), "after": (meta["ts"] >= split.t_test).to_numpy(),
             "complete": (meta["cycle"] > 0).to_numpy()}
    masks["unseen"] = meta["bin_id"].isin(test_bins).to_numpy() & masks["after"] & masks["complete"] & pre
    unseen_idx = np.flatnonzero(masks["unseen"])
    naive = point_metrics(y[unseen_idx], feats.X["naive_kg_m3"].fillna(0).to_numpy()[unseen_idx]
                          * meta["container_volume_m3"].to_numpy()[unseen_idx])
    ns = [k for k in sizes if k < len(pool)] + [len(pool)]
    tasks = [(n, r, list(np.random.default_rng(s.seed + 7919 * r + n).choice(pool, n, replace=False)))
             for n in ns for r in range(repeats_for(n, len(pool)))]
    workers = max(1, min(len(tasks), (os.cpu_count() or 4) // THREADS_PER_FIT))
    _SHARED.update(feats=feats, y=y, pre=pre, masks=masks, ref=ref,
                   s=dataclasses.replace(s, num_threads=THREADS_PER_FIT))
    log.info("%d fits (%d sizes) on %d parallel workers x %d threads", len(tasks), len(ns), workers, THREADS_PER_FIT)
    results: dict = {}
    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork")) as pool_exec:
        for n, r, out in pool_exec.map(_worker, sorted(tasks, key=lambda t: -t[0])):
            results.setdefault(n, []).append(out)
    points = []
    for n in ns:
        runs = results[n]
        point = {"n_machines": int(n), "repeats": len(runs), "unseen": _summarise(runs, "unseen"),
                 "same": _summarise(runs, "same")}
        points.append(point)
        log.info("  %s n=%d (x%d): unseen %.2f %% [%.2f-%.2f], same %.2f %%", material, n, len(runs),
                 point["unseen"]["mean_pct"], point["unseen"]["min_pct"], point["unseen"]["max_pct"],
                 point["same"]["mean_pct"])
    result = {"material": material, "data": str(data_dir),
              "reference_model": {k: ref[k] for k in ("params", "best_iter", "monotone")},
              "n_features": len(ref["features"]), "train_pool_machines": int(len(pool)), "test_machines": len(test_bins),
              "test_lifts_unseen": int(unseen_idx.size), "test_start": str(split.t_test),
              "naive_unseen_mean_pct": naive.get("mean_pct"), "points": points,
              "wall_s": round(time.perf_counter() - t0, 1)}
    _write(models_dir, result)
    return result


def _write(models_dir: Path, res: dict) -> None:
    mat = res["material"]
    (models_dir / f"material_curve_{mat}.json").write_text(json.dumps(res, indent=2))
    rows = [{"n_machines": p["n_machines"], "repeats": p["repeats"], "test": t, **p[t]}
            for p in res["points"] for t in ("unseen", "same")]
    pd.DataFrame(rows).to_csv(models_dir / f"material_curve_{mat}.csv", index=False)
    plot_material_curve(res, models_dir / "plots" / f"material_curve_{mat}.png")


def plot_material_curve(res: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pts = res["points"]
    n = np.array([p["n_machines"] for p in pts])
    fig, ax = plt.subplots(figsize=(8, 4.6))
    for test, color, label in (("unseen", "#2a78d6", "unseen machines (same held-out set for every n)"),
                               ("same", "#d9822b", "same machines, later months")):
        mean = np.array([p[test]["mean_pct"] for p in pts])
        lo = np.array([p[test]["min_pct"] for p in pts])
        hi = np.array([p[test]["max_pct"] for p in pts])
        ax.fill_between(n, lo, hi, color=color, alpha=0.15, linewidth=0)
        ax.plot(n, mean, marker="o", color=color, label=label)
    ax.axhline(TARGET_PCT, color="#888", linestyle="--", linewidth=1, label="5 % target")
    ax.set_xscale("log", base=2)
    ax.set_xticks(n, [str(v) for v in n])
    ax.set(xlabel="training machines (log scale)", ylabel="mean % error at lift",
           title=f"{res['material']}: error vs number of training machines (band = min-max over repeats)")
    ax.set_ylim(bottom=0)
    ax.grid(alpha=0.3)
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
