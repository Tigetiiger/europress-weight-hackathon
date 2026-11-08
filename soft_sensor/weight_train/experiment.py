"""Training + evaluation for one split: tuning, setup selection, intervals, ladder, learning curve."""
from __future__ import annotations

import logging
import multiprocessing as mp
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .bundle import SoftSensor, model_key
from .conformal import Conformal
from .features import FEATURE_SETS, Features, add_pressure_density, base_features, pre_lift_mask
from .metrics import evaluate, lift_breakdowns, point_metrics
from .modeling import FittedModel, Rows, fit, lift_pct_error, make_rows
from .physics import PressureInverter
from .settings import NOMINAL_DENSITY, TrainSettings
from .splits import SPLIT_DESCRIPTIONS, Split, make_split

log = logging.getLogger(__name__)
FULL = FEATURE_SETS["full"]


@dataclass
class TrainData:
    base: Features
    y_kg: np.ndarray
    false_full_cycle: np.ndarray
    pre_lift: np.ndarray


@dataclass
class SplitOutput:
    sensor: SoftSensor
    metrics: dict
    test_frame: pd.DataFrame
    shap: dict = field(default_factory=dict)
    importance: dict = field(default_factory=dict)


_LOAD: dict = {}   # set before forking feature workers (read copy-on-write)


def _feature_chunk(bins: list) -> Features:
    hourly, strokes = _LOAD["hourly"], _LOAD["strokes"]
    h = hourly[hourly["bin_id"].isin(bins)]
    st = strokes[strokes["bin_id"].isin(bins)] if strokes is not None else None
    return base_features(h, st)


def parallel_base_features(hourly: pd.DataFrame, strokes: pd.DataFrame | None, workers: int) -> Features:
    """base_features per contiguous chunk of machines on ``workers`` processes (features never cross machines)."""
    bins = sorted(hourly["bin_id"].unique())
    if workers <= 1 or len(bins) < 2:
        return base_features(hourly, strokes)
    chunks = [list(c) for c in np.array_split(np.array(bins, dtype=object), min(workers, len(bins))) if len(c)]
    _LOAD.update(hourly=hourly, strokes=strokes)
    try:
        with ProcessPoolExecutor(max_workers=len(chunks), mp_context=mp.get_context("fork")) as ex:
            parts = list(ex.map(_feature_chunk, chunks))
    finally:
        _LOAD.clear()
    offset = 0
    metas = []
    for part in parts:                       # keep segment ids unique across chunks
        meta = part.meta.copy()
        meta["seg"] = meta["seg"] + offset
        offset = int(meta["seg"].max()) + 1
        metas.append(meta)
    X = pd.concat([part.X for part in parts], ignore_index=True)
    return Features(X=X, meta=pd.concat(metas, ignore_index=True))


def load_training_data(run_dir: Path, material: str | None = None, workers: int | None = None) -> TrainData:
    filters = [("material", "==", material)] if material else None
    hourly = pd.read_parquet(run_dir / "hourly.parquet", filters=filters)
    strokes_path = run_dir / "strokes.parquet"
    strokes = None
    if strokes_path.is_file():
        bin_filter = [("bin_id", "in", sorted(hourly["bin_id"].unique()))] if material else None
        strokes = pd.read_parquet(strokes_path, columns=["bin_id", "ts", "peak_pressure_bar", "duration_s"],
                                  filters=bin_filter)
        strokes["bin_id"] = strokes["bin_id"].astype(str)
    t0 = time.perf_counter()
    base = parallel_base_features(hourly, strokes, workers if workers is not None else (os.cpu_count() or 1))
    log.info("features for %d rows built in %.1f s", len(base.X), time.perf_counter() - t0)
    truth = hourly.sort_values(["bin_id", "ts"], kind="stable").reset_index(drop=True)  # same order as features
    ff = truth["true_false_full"].astype(bool) if "true_false_full" in truth else pd.Series(False, index=truth.index)
    ff_cycle = ff.groupby(base.meta["seg"].to_numpy()).cummax().to_numpy()
    return TrainData(base, truth["true_mass_kg"].to_numpy(dtype=float), ff_cycle, pre_lift_mask(base.meta))


# ----------------------------------------------------------------------------- helpers
def _features(setup: str, feature_set: str = "full") -> list[str]:
    cols = list(FEATURE_SETS[feature_set])
    return cols + ["material"] if setup == "shared" else cols


def _val_error(model: FittedModel, X, rows: Rows, vol, pre) -> float:
    idx = rows.idx[pre[rows.idx]]
    if idx.size == 0:
        return float("nan")
    return lift_pct_error(rows.y[pre[rows.idx]] * vol[idx], model.predict(X.iloc[idx]) * vol[idx])


@dataclass
class Ctx:
    X: pd.DataFrame
    y: np.ndarray
    vol: np.ndarray
    pre: np.ndarray
    material: np.ndarray
    bins: np.ndarray
    split: Split
    s: TrainSettings
    rng: np.random.Generator

    def rows(self, which: str, group: str) -> Rows:
        mask = getattr(self.split, which)
        if group != "all":
            mask = mask & (self.material == group)
        return make_rows(mask, self.pre, self.y, self.s, self.rng)


def _tune_shared(ctx: Ctx) -> tuple[FittedModel, float, list]:
    train, val = ctx.rows("train", "all"), ctx.rows("val", "all")
    trials = []
    best, best_err = None, np.inf
    for params in ctx.s.grid:
        m = fit(ctx.X, train, val, _features("shared"), ctx.s, params)
        err = _val_error(m, ctx.X, val, ctx.vol, ctx.pre)
        trials.append({**params, "best_iter": m.best_iter, "val_lift_mean_pct": err})
        log.info("  grid %s -> val lift %.2f %% (%d rounds)", params, err, m.best_iter)
        if err < best_err:
            best, best_err = m, err
    return best, best_err, trials


def _maybe_monotone(ctx: Ctx, model: FittedModel, err: float, group: str, setup: str):
    train, val = ctx.rows("train", group), ctx.rows("val", group)
    mono = fit(ctx.X, train, val, _features(setup), ctx.s, model.params, monotone=True)
    err_mono = _val_error(mono, ctx.X, val, ctx.vol, ctx.pre)
    keep = np.isfinite(err_mono) and (not np.isfinite(err) or err_mono <= err * 1.02)
    return (mono, err_mono, True) if keep else (model, err, False)


def _fit_quantiles(ctx: Ctx, sensor: SoftSensor, setup: str, group: str, point: FittedModel) -> None:
    train, val = ctx.rows("train", group), ctx.rows("val", group)
    for kind, alpha in zip(("q_lo", "q_hi"), ctx.s.quantiles):
        q = fit(ctx.X, train, val, point.features, ctx.s, point.params, objective="quantile", alpha=alpha)
        sensor.models[model_key(setup, group, kind)] = q


def _train_models(ctx: Ctx, sensor: SoftSensor, materials: list[str], setup_mode: str) -> dict:
    """Shared (tuned) + per-material models, monotone check, setup selection, quantiles."""
    shared, err_shared, trials = _tune_shared(ctx)
    shared, err_shared, mono_shared = _maybe_monotone(ctx, shared, err_shared, "all", "shared")
    sensor.models[model_key("shared", "all", "point")] = shared
    info = {"grid": trials, "params": shared.params, "monotone": {"shared": mono_shared}, "val_lift_mean_pct": {}}
    for mat in materials:
        val_rows = ctx.rows("val", mat)
        per = fit(ctx.X, ctx.rows("train", mat), val_rows, _features("per_material"), ctx.s, shared.params)
        err_per = _val_error(per, ctx.X, val_rows, ctx.vol, ctx.pre)
        per, err_per, mono = _maybe_monotone(ctx, per, err_per, mat, "per_material")
        sensor.models[model_key("per_material", mat, "point")] = per
        err_sh = _val_error(shared, ctx.X, val_rows, ctx.vol, ctx.pre)
        choose_per = np.isfinite(err_per) and np.isfinite(err_sh) and err_per < err_sh
        auto = "per_material" if choose_per else "shared"
        sensor.selection[mat] = auto if setup_mode == "both" else setup_mode
        info["monotone"][mat] = mono
        info["val_lift_mean_pct"][mat] = {"shared": err_sh, "per_material": err_per}
        log.info("  %s: val lift shared %.2f %% vs per-material %.2f %% -> %s", mat, err_sh, err_per,
                 sensor.selection[mat])
    for setup, group in {(sensor.selection[m], "all" if sensor.selection[m] == "shared" else m) for m in materials}:
        _fit_quantiles(ctx, sensor, setup, group, sensor.models[model_key(setup, group, "point")])
    return info


def _calibrate(ctx: Ctx, sensor: SoftSensor) -> None:
    cal = ctx.rows("cal", "all")
    if cal.idx.size == 0:  # tiny fleets: fall back to validation rows
        cal = ctx.rows("val", "all")
    Xc, mat = ctx.X.iloc[cal.idx], ctx.material[cal.idx]
    point, lo, hi = sensor.predict_kg_m3(Xc, mat)
    sensor.conformal = Conformal.fit(lo, hi, point, cal.y, mat, Xc["fill_ffill"].to_numpy(),
                                     ctx.s.fill_buckets, ctx.s.target_coverage, ctx.s.min_cal_points,
                                     lift_rows=ctx.pre[cal.idx])


def _frame(ctx: Ctx, idx: np.ndarray, pred, lo=None, hi=None) -> pd.DataFrame:
    d = {"y_kg": ctx.y[idx] * ctx.vol[idx], "pred_kg": pred * ctx.vol[idx],
         "material": ctx.material[idx], "is_lift": ctx.pre[idx]}
    if lo is not None:
        d.update(lo_kg=lo * ctx.vol[idx], hi_kg=hi * ctx.vol[idx])
    return pd.DataFrame(d)


def _ladder(ctx: Ctx, sensor: SoftSensor, test_idx: np.ndarray) -> dict:
    """naive fill x density -> fill+presses -> +pressure -> +weather (shared setup), plus setups."""
    lift = test_idx[ctx.pre[test_idx]]
    X = ctx.X.iloc[lift]
    preds = {"naive_fill_x_density": X["naive_kg_m3"].fillna(0).to_numpy()}
    shared = sensor.models[model_key("shared", "all", "point")]
    for step in ("fill_presses", "pressure", "pressure_graph"):
        m = fit(ctx.X, ctx.rows("train", "all"), ctx.rows("val", "all"), _features("shared", step), ctx.s,
                shared.params)
        preds[step] = m.predict(X)
    preds["full_shared"] = shared.predict(X)
    mats = ctx.material[lift]
    per = np.full(len(lift), np.nan)
    for mat in np.unique(mats):
        key = model_key("per_material", mat, "point")
        if key in sensor.models:
            per[mats == mat] = sensor.models[key].predict(X[mats == mat])
    preds["full_per_material"] = per
    preds["full_selected"] = sensor.predict_kg_m3(X, mats)[0]
    out = {}
    for step, p in preds.items():
        f = _frame(ctx, lift, p)
        out[step] = {"overall": point_metrics(f.y_kg, f.pred_kg),
                     "by_material": {m: point_metrics(g.y_kg, g.pred_kg) for m, g in f.groupby("material")}}
    return out


def _stratified_machines(bins: np.ndarray, bin_material: dict, n: int, rng: np.random.Generator) -> list:
    """n machines with material shares as in the training fleet, at least 1 per material."""
    mats = sorted({bin_material[b] for b in bins})
    pools = {m: [b for b in bins if bin_material[b] == m] for m in mats}
    share = np.array([len(pools[m]) for m in mats], dtype=float) / len(bins)
    counts = np.maximum(np.floor(share * n).astype(int), 1 if n >= len(mats) else 0)
    for i in np.argsort(-(share * n - np.floor(share * n)), kind="stable"):
        if counts.sum() >= n:
            break
        counts[i] += 1
    counts = np.minimum(counts, [len(pools[m]) for m in mats])
    return [b for m, k in zip(mats, counts) for b in rng.choice(pools[m], k, replace=False)]


def _mean_metrics(items: list[dict]) -> dict:
    keys = {k for d in items for k, v in d.items() if isinstance(v, (int, float))}
    return {k: float(np.nanmean([d.get(k, np.nan) for d in items])) for k in sorted(keys)}


def _learning_curve(ctx: Ctx, shared: FittedModel, test_idx: np.ndarray) -> list[dict]:
    """Error vs number of training machines: stratified by material, 3 repeats for small n."""
    lift = test_idx[ctx.pre[test_idx]]
    bins = np.array(ctx.split.train_bins)
    first = pd.Series(ctx.material).groupby(ctx.bins).first().to_dict()
    sizes = [n for n in ctx.s.learning_curve_sizes if n < len(bins)] + [len(bins)]
    out = []
    for n in sizes:
        repeats = 3 if n <= 50 and n < len(bins) else 1
        runs = []
        for r in range(repeats):
            chosen = _stratified_machines(bins, first, n, np.random.default_rng(ctx.s.seed + 1000 * r + n))
            rows = make_rows(ctx.split.train & np.isin(ctx.bins, chosen), ctx.pre, ctx.y, ctx.s, ctx.rng)
            m = fit(ctx.X, rows, None, shared.features, ctx.s, shared.params, rounds=shared.best_iter)
            runs.append(_frame(ctx, lift, m.predict(ctx.X.iloc[lift])))
        overall = _mean_metrics([point_metrics(f.y_kg, f.pred_kg) for f in runs])
        by_mat = {k: _mean_metrics([point_metrics(g.y_kg, g.pred_kg)
                                    for f in runs for kk, g in f.groupby("material") if kk == k])
                  for k in sorted(set(ctx.material[lift]))}
        out.append({"n_machines": int(n), "repeats": repeats, "overall": overall, "by_material": by_mat})
        log.info("  learning curve n=%d (x%d) -> %.2f %%", n, repeats, overall["mean_pct"])
    return out


def _explain(ctx: Ctx, sensor: SoftSensor, test_idx: np.ndarray) -> tuple[dict, dict]:
    importance, shap = {}, {}
    lift = test_idx[ctx.pre[test_idx]]
    for mat in sorted(set(ctx.material[lift])):
        setup, group = sensor._group(mat)
        model = sensor.models[model_key(setup, group, "point")]
        gain = model.booster.feature_importance("gain", iteration=model.best_iter)
        importance[mat] = dict(sorted(zip(model.features, map(float, gain)), key=lambda kv: -kv[1]))
        idx = lift[ctx.material[lift] == mat][: ctx.s.shap_rows]
        Xm = ctx.X.iloc[idx][model.features]
        contrib = model.booster.predict(Xm, num_iteration=model.best_iter, pred_contrib=True)
        shap[mat] = (pd.DataFrame(contrib[:, :-1], columns=[f"shap__{c}" for c in model.features])
                     .join(Xm.reset_index(drop=True).add_prefix("value__")))
    return importance, shap


def run_split(data: TrainData, kind: str, s: TrainSettings, setup_mode: str = "both") -> SplitOutput:
    t0 = time.perf_counter()
    split = make_split(data.base.meta, kind, s)
    meta = data.base.meta
    vol = meta["container_volume_m3"].to_numpy()
    y = data.y_kg / vol
    tr = split.train
    inverter = PressureInverter.fit(data.base.X["p_now"][tr], data.base.X["fill_ffill"][tr],
                                    data.base.X["temp_c"][tr], meta["material"][tr], y[tr])
    feats = add_pressure_density(data.base, inverter)
    ctx = Ctx(feats.X, y, vol, data.pre_lift, meta["material"].to_numpy().astype(str),
              meta["bin_id"].to_numpy(), split, s, np.random.default_rng(s.seed))
    sensor = SoftSensor(inverter, info={"split": kind})
    materials = sorted(set(ctx.material[split.train]))
    log.info("[%s] train %d / val %d / cal %d / test %d rows", kind, tr.sum(), split.val.sum(),
             split.cal.sum(), split.test.sum())
    train_info = _train_models(ctx, sensor, materials, setup_mode)
    _calibrate(ctx, sensor)
    test_idx = np.flatnonzero(split.test)
    point, lo, hi = sensor.predict_kg_m3(ctx.X.iloc[test_idx], ctx.material[test_idx])
    frame = _frame(ctx, test_idx, point, lo, hi)
    frame = frame.assign(bin_id=meta["bin_id"].to_numpy()[test_idx], ts=meta["ts"].iloc[test_idx].reset_index(drop=True),
                         false_full_in_cycle=data.false_full_cycle[test_idx],
                         **{c: ctx.X[c].to_numpy()[test_idx] for c in
                            ["infeed", "drainage", "equipment_class", "container_volume_m3", "fill_ffill"]
                            + [f"rain_mm_d{k}" for k in range(1, 8)]})
    metrics = {"description": SPLIT_DESCRIPTIONS[kind], "n_train_machines": len(split.train_bins),
               "n_test_machines": len(split.test_bins), "test_start": str(split.t_test),
               "rows": {k: int(getattr(split, k).sum()) for k in ("train", "val", "cal", "test")},
               "selection": dict(sensor.selection), "training": train_info,
               "final": evaluate(frame), "breakdowns": lift_breakdowns(frame[frame["is_lift"]]),
               "ladder": _ladder(ctx, sensor, test_idx), "inverter": inverter.to_dict()}
    if kind == "bins":
        metrics["learning_curve"] = _learning_curve(ctx, sensor.models[model_key("shared", "all", "point")],
                                                    test_idx)
    importance, shap = _explain(ctx, sensor, test_idx)
    metrics["feature_importance_gain"] = importance
    metrics["train_wall_s"] = time.perf_counter() - t0
    sensor.info.update(nominal_density=NOMINAL_DENSITY, feature_sets=FEATURE_SETS)
    return SplitOutput(sensor, metrics, frame, shap, importance)
