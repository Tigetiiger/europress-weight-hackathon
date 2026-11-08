"""REPORT.md for a trained folder, generated from metrics.json + test predictions (no hard-coded numbers)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .modeling import MIN_LIFT_KG_FOR_PCT
from .plots import LADDER_STEPS, SPLIT_LABEL, TARGET_PCT, load_metrics, materials_of, metric, splits_of

log = logging.getLogger(__name__)

SMALL_N_LIFTS = 30
LEARNING_TOLERANCE_PP = 0.5
TOP_FEATURES = 5
BIAS_FLAG_PCT = 2.0
COVERAGE_SLACK = 0.05
OUTLIER_RATIO = 1.5        # mean / median above this = outlier-driven
OUTLIER_RATIO_BROAD = 0.8  # median above this share of the target = broad-based error
WORST3_SHARE = 0.25
SUBSET_JUMP = 3.0  # smallest-size error / full-size error per material
DASH = "–"
BREAKDOWN_TITLES = {"infeed": "Infeed type", "drainage": "Drainage (0 = no, 1 = yes)",
                    "equipment_class": "Equipment class", "container_size": "Container size",
                    "false_full_in_cycle": "False-full episode in the cycle", "week": "Weather in the week before lift"}


# ---------------------------------------------------------------- formatting
def _num(x: float | None, nd: int = 1) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return DASH
    return DASH if np.isnan(v) else f"{v:,.{nd}f}"


def _pct(frac: float | None, nd: int = 0) -> str:
    v = _num(None if frac is None else float(frac) * 100, nd)
    return v if v == DASH else f"{v} %"


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    return "\n".join(lines + ["| " + " | ".join(r) + " |" for r in rows])


def _img(models_dir: Path, name: str, caption: str) -> str:
    return f"![{caption}](plots/{name}.png)" if (models_dir / "plots" / f"{name}.png").exists() else ""


def _block(sp: dict, scope: str, group: str) -> dict:
    b = sp.get("final", {}).get(scope, {})
    return (b.get("overall") if group == "overall" else b.get("by_material", {}).get(group)) or {}


def _split_title(split: str) -> str:
    return f"`{split}` ({SPLIT_LABEL.get(split, split)})"


# ---------------------------------------------------------------- data helpers
def _lift_frame(models_dir: Path, split: str) -> pd.DataFrame | None:
    path = models_dir / split / "test_predictions.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    lift = df[df["is_lift"]].copy()
    ok = lift["y_kg"] >= MIN_LIFT_KG_FOR_PCT
    lift["pct"] = np.where(ok, (lift["pred_kg"] - lift["y_kg"]).abs() / lift["y_kg"].where(ok) * 100, np.nan)
    return lift


def _fleet_machines(models_dir: Path) -> dict[str, int]:
    """Machines per material in the whole fleet: the time split tests every machine."""
    path = models_dir / "time" / "test_predictions.parquet"
    if not path.exists():
        return {}
    df = pd.read_parquet(path, columns=["material", "bin_id"])
    return {str(k): int(v) for k, v in df.groupby("material")["bin_id"].nunique().items()}


def _ladder_value(sp: dict, step: str, group: str) -> float:
    step_d = sp.get("ladder", {}).get(step, {})
    return metric(step_d.get("overall") if group == "overall" else step_d.get("by_material", {}).get(group),
                  "mean_pct")


# ---------------------------------------------------------------- sections
def _summary(metrics: dict, mats: list[str]) -> str:
    splits = splits_of(metrics)
    if not splits:
        return "No evaluation splits found in metrics.json."
    parts = []
    for split in splits:
        sp = metrics["splits"][split]
        o = _block(sp, "lift", "overall")
        naive = _ladder_value(sp, "naive_fill_x_density", "overall")
        ok = [m for m in mats if metric(_block(sp, "lift", m), "mean_pct") < TARGET_PCT]
        miss = [m for m in mats if metric(_block(sp, "lift", m), "mean_pct") >= TARGET_PCT]
        parts.append(
            f"On the {_split_title(split)} split the selected soft sensor estimates the mass in the hour before "
            f"emptying with a mean error of **{_num(o.get('mean_pct'))} %** (median {_num(o.get('median_pct'))} %, "
            f"{_pct(o.get('within_5pct'))} of lifts within 5 %, n = {o.get('n', DASH)}), against "
            f"{_num(naive)} % for the naive fill × nominal density estimate; {len(ok)} of {len(ok) + len(miss)} "
            f"materials meet the < {TARGET_PCT:.0f} % target" + (f" (missing: {', '.join(miss)})." if miss else ".")
            + f" The calibrated p10–p90 interval covers {_pct(o.get('coverage'))} of lift masses (target "
            f"{_pct(metrics.get('settings', {}).get('target_coverage', 0.8))}).")
    return " ".join(parts) + ("\n\n> **All data is synthetic** (simulator output for the Europress hackathon). "
                              "The numbers demonstrate the method, not field performance.")


def _compute(models_dir: Path, metrics: dict) -> str:
    c = metrics.get("compute", {})
    rows = [["training wall time (all splits)", f"{_num(c.get('train_wall_s'), 0)} s"],
            ["feature build", f"{_num(c.get('feature_build_s'))} s"],
            ["peak memory (RSS)", f"{_num(c.get('peak_rss_gb'), 2)} GB"],
            ["hourly rows / machines", f"{_num(c.get('rows'), 0)} / {c.get('machines', DASH)}"]]
    rows += [[f"split `{s}` wall time", f"{_num(metrics['splits'][s].get('train_wall_s'), 0)} s"]
             for s in splits_of(metrics)]
    stats_path = models_dir / "predict_stats.json"
    if stats_path.exists():
        p = json.loads(stats_path.read_text())
        rows += [["prediction run", f"{_num(p.get('rows'), 0)} rows in {_num(p.get('wall_s'))} s "
                                    f"(features + predict {_num(p.get('features_and_predict_s'))} s)"],
                 ["prediction cost", f"{_num(p.get('us_per_machine_hour'))} µs per machine-hour"],
                 ["prediction peak memory", f"{_num(p.get('peak_rss_gb'), 2)} GB"]]
    return "## Compute\n\n" + _table(["item", "value"], rows)


LIFT_HEADER = ["material", "n", "MAE kg", "mean %", "median %", "within 5 %", "within 10 %", "bias kg",
               "coverage", "width kg"]


def _lift_row(name: str, m: dict) -> list[str]:
    return [name, str(m.get("n", DASH)), _num(m.get("mae_kg"), 0), _num(m.get("mean_pct")),
            _num(m.get("median_pct")), _pct(m.get("within_5pct")), _pct(m.get("within_10pct")),
            _num(m.get("bias_kg"), 0), _pct(m.get("coverage")), _num(m.get("width_kg"), 0)]


def _split_section(models_dir: Path, split: str, sp: dict, mats: list[str]) -> str:
    r = sp.get("rows", {})
    sel = sp.get("selection", {})
    present = [m for m in mats if m in sp.get("final", {}).get("lift", {}).get("by_material", {})]
    lift_rows = [_lift_row(m, _block(sp, "lift", m)) for m in present] + \
        [_lift_row("**overall**", _block(sp, "lift", "overall"))]
    hourly_rows = [[m, _num(_block(sp, "hourly", m).get("mae_kg"), 0), _num(_block(sp, "hourly", m).get("mean_pct")),
                    _pct(_block(sp, "hourly", m).get("coverage"))] for m in present + ["overall"]]
    desc = sp.get("description", "")
    return "\n\n".join(filter(None, [
        f"## Split {_split_title(split)}",
        f"{desc[:1].upper() + desc[1:]}. Training machines: {sp.get('n_train_machines', DASH)}, test machines: "
        f"{sp.get('n_test_machines', DASH)}, test from {sp.get('test_start', DASH)}. Rows: train "
        f"{_num(r.get('train'), 0)}, val {_num(r.get('val'), 0)}, cal {_num(r.get('cal'), 0)}, "
        f"test {_num(r.get('test'), 0)}.",
        "Selected setup: " + ", ".join(f"{m} → **{sel[m]}**" for m in mats if m in sel) + ".",
        "**At lift (hour before emptying vs weighbridge):**", _table(LIFT_HEADER, lift_rows),
        "**Hourly (every test hour):**", _table(["material", "MAE kg", "mean %", "coverage"], hourly_rows),
        _img(models_dir, f"pred_vs_true_{split}", f"predicted vs true at lift, {split}")]))


def _ladder_section(models_dir: Path, metrics: dict, mats: list[str]) -> str:
    out = ["## Comparison ladder (mean % error at lift)",
           "Each step adds signals: naive fill × nominal density → fill + press counts (LightGBM) → + peak "
           "hydraulic pressure → + pressure graph (pressure along the ram stroke: contact point, rise slope, mean "
           "pressure, completion) → + weather (full model, shared across materials); then the full per-material "
           "models and the setup selected per material on validation. The reported fill level itself is the "
           "machine's approximate, pressure-derived estimate."]
    for split in splits_of(metrics):
        sp = metrics["splits"][split]
        steps = [s for s in LADDER_STEPS if s in sp.get("ladder", {})]
        if not steps:
            continue
        rows = [[LADDER_STEPS[s].replace("\n", " ")] + [_num(_ladder_value(sp, s, g)) for g in mats + ["overall"]]
                for s in steps]
        out += [f"**{_split_title(split)}**", _table(["step"] + mats + ["**overall**"], rows)]
    out.append(_img(models_dir, "ladder", "comparison ladder"))
    return "\n\n".join(filter(None, out))


def _setup_section(metrics: dict, mats: list[str]) -> str:
    rows = []
    for split in splits_of(metrics):
        sp = metrics["splits"][split]
        val = sp.get("training", {}).get("val_lift_mean_pct", {})
        for m in mats:
            if m not in sp.get("selection", {}):
                continue
            ts, tp = _ladder_value(sp, "full_shared", m), _ladder_value(sp, "full_per_material", m)
            best = DASH if np.isnan(ts) or np.isnan(tp) else ("shared" if ts <= tp else "per_material")
            rows.append([m, split, _num(val.get(m, {}).get("shared")), _num(val.get(m, {}).get("per_material")),
                         _num(ts), _num(tp), sp["selection"][m], best])
    head = ["material", "split", "val shared", "val per-material", "test shared", "test per-material",
            "selected", "better on test"]
    note = ("Setup is chosen per material on the validation lifts (mean % error); `–` means no validation "
            "lifts of that material, in which case the shared model is kept.")
    return "## Per-material vs shared model\n\n" + note + "\n\n" + _table(head, rows)


def _coverage_section(models_dir: Path, metrics: dict, mats: list[str]) -> str:
    s = metrics.get("settings", {})
    q = s.get("quantiles", [0.1, 0.9])
    edges = s.get("fill_buckets", [])
    text = (f"Intervals come from LightGBM quantile models (p{q[0] * 100:.0f}/p{q[-1] * 100:.0f}) calibrated by "
            "**normalised conformalised quantile regression (CQR)**: on a separate calibration slice (machines/hours "
            "used neither for training nor for validation) the score max(q_lo − y, y − q_hi) / width is computed and "
            f"the interval is widened by its finite-sample {_pct(s.get('target_coverage', 0.8))} quantile. "
            f"Calibration is **Mondrian** by material × fill bucket (fill edges {', '.join(map(str, edges[1:-1]))} %); "
            f"strata with fewer than {s.get('min_cal_points', DASH)} calibration points fall back to the material, "
            "then to the pool.")
    rows = []
    for split in splits_of(metrics):
        sp = metrics["splits"][split]
        for g in [m for m in mats if _block(sp, "lift", m)] + ["overall"]:
            lift, hour = _block(sp, "lift", g), _block(sp, "hourly", g)
            rows.append([g, split, _pct(lift.get("coverage")), _num(lift.get("width_kg"), 0),
                         _pct(hour.get("coverage")), _num(hour.get("width_kg"), 0)])
    table = _table(["material", "split", "lift coverage", "lift width kg", "hourly coverage", "hourly width kg"],
                   rows)
    notes = _coverage_notes(models_dir, metrics, mats)
    return "\n\n".join(filter(None, [f"## Interval coverage (target {_pct(s.get('target_coverage', 0.8))})", text,
                                     table, notes, _img(models_dir, "coverage_by_material", "interval coverage")]))


def _wide_strata(models_dir: Path, metrics: dict) -> list[str]:
    """Material x fill-bucket strata whose mean interval is wider than the mean true mass."""
    edges = metrics.get("settings", {}).get("fill_buckets", [0.0, 50.0, 80.0, 1000.0])
    out = []
    for split in splits_of(metrics):
        path = models_dir / split / "test_predictions.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path, columns=["material", "fill_ffill", "y_kg", "lo_kg", "hi_kg"])
        df = df.assign(width=df["hi_kg"] - df["lo_kg"],
                       bucket=pd.cut(df["fill_ffill"].fillna(0), edges, include_lowest=True, right=False))
        agg = df.groupby(["material", "bucket"], observed=True).agg(w=("width", "mean"), y=("y_kg", "mean"))
        for (m, b), r in agg[agg["w"] > agg["y"]].iterrows():
            out.append(f"{m}/{split}, fill {b.left:.0f}–{b.right:.0f} %: mean width {_num(r['w'], 0)} kg for a "
                       f"mean mass of {_num(r['y'], 0)} kg")
    return out


def _coverage_notes(models_dir: Path, metrics: dict, mats: list[str]) -> str:
    target = metrics.get("settings", {}).get("target_coverage", 0.8)
    notes = []
    covs = {(m, s): metric(_block(metrics["splits"][s], "lift", m), "coverage") for s in splits_of(metrics)
            for m in mats}
    low = [f"{m}/{s} ({_pct(c)})" for (m, s), c in covs.items() if c < target - COVERAGE_SLACK]
    if low:
        notes.append(f"Lift coverage more than {COVERAGE_SLACK * 100:.0f} pp below target: {', '.join(low)}. "
                     "Calibration pools all hours of a stratum, while the lift hour is the fullest, hardest hour "
                     "of a cycle; on unseen machines the calibration machines are also few, so their error scale "
                     "need not transfer.")
    wide = _wide_strata(models_dir, metrics)
    if wide:
        notes.append("Suspiciously wide intervals (wider than the mass itself): " + "; ".join(wide) + ".")
    return "\n\n".join(notes)


def _breakdown_section(metrics: dict) -> str:
    splits = splits_of(metrics)
    out = ["## Error breakdowns (lift, selected model)"]
    for dim, title in BREAKDOWN_TITLES.items():
        values: list[str] = []
        for s in splits:
            values += [v for v in metrics["splits"][s].get("breakdowns", {}).get(dim, {}) if v not in values]
        if not values:
            continue
        rows = [[v] + [c for s in splits for c in (
            _num(metrics["splits"][s].get("breakdowns", {}).get(dim, {}).get(v, {}).get("mean_pct")),
            str(metrics["splits"][s].get("breakdowns", {}).get(dim, {}).get(v, {}).get("n", DASH)))] for v in values]
        out += [f"**{title}**", _table([dim] + [c for s in splits for c in (f"{s} mean %", f"{s} n")], rows)]
    return "\n\n".join(out)


def _learning_verdict(curve: list[dict]) -> str:
    final, last_n = metric(curve[-1].get("overall"), "mean_pct"), curve[-1]["n_machines"]
    enough = next((c["n_machines"] for c in curve
                   if metric(c.get("overall"), "mean_pct") <= final + LEARNING_TOLERANCE_PP), last_n)
    if len(curve) == 1:
        return f"Only one training size ({last_n} machines) was evaluated, so the curve cannot be read yet."
    if enough == last_n:
        return (f"No size below the full {last_n} training machines gets within {LEARNING_TOLERANCE_PP} pp of the "
                f"all-machines {_num(final)} %, so by this rule a pilot needs all {last_n} and the curve has not "
                "flattened yet.")
    return (f"A pilot needs about **{enough} machines**: the smallest size whose error on unseen machines is within "
            f"{LEARNING_TOLERANCE_PP} pp of the all-machines value ({_num(final)} %).")


def _learning_shape(curve: list[dict], mats: list[str]) -> str:
    ys = [metric(c.get("overall"), "mean_pct") for c in curve]
    if len(ys) < 2:
        return ""
    drops = np.diff(ys)
    i = int(np.nanargmin(drops))
    out = [f"The largest gain is from {curve[i]['n_machines']} to {curve[i + 1]['n_machines']} machines "
           f"({_num(ys[i])} → {_num(ys[i + 1])} %)."]
    if np.nanmax(drops) > 0:
        out.append("The curve is not monotone (each size is one random subset, so it is noisy).")
    ratio = {m: metric(curve[0].get("by_material", {}).get(m), "mean_pct")
             / metric(curve[-1].get("by_material", {}).get(m), "mean_pct") for m in mats}
    jumps = [m for m, r in ratio.items() if r > SUBSET_JUMP]
    if jumps:
        out.append(f"At {curve[0]['n_machines']} machines {', '.join(jumps)} are more than {SUBSET_JUMP:.0f}× worse "
                   "than with all machines: the random subsets are not stratified by material, so a small pilot "
                   "may contain few or none of a material.")
    return " ".join(out)


def _learning_section(models_dir: Path, metrics: dict, mats: list[str]) -> str:
    curve = metrics.get("splits", {}).get("bins", {}).get("learning_curve") or []
    if not curve:
        return ""
    rows = [[str(c["n_machines"]), _num(metric(c.get("overall"), "mean_pct"))]
            + [_num(metric(c.get("by_material", {}).get(m), "mean_pct")) for m in mats] for c in curve]
    verdict = " ".join([_learning_verdict(curve), _learning_shape(curve, mats)]).strip()
    return "\n\n".join(filter(None, [
        "## Learning curve (unseen machines, shared model)",
        _table(["training machines", "**overall**"] + mats, rows), verdict,
        _img(models_dir, "learning_curve", "learning curve")]))


def _importance_section(models_dir: Path, metrics: dict, mats: list[str]) -> str:
    rows = []
    for split in splits_of(metrics):
        sp = metrics["splits"][split]
        for m, gains in sp.get("feature_importance_gain", {}).items():
            total = sum(gains.values()) or 1.0
            top = sorted(gains.items(), key=lambda kv: -kv[1])[:TOP_FEATURES]
            rows.append([m, split, sp.get("selection", {}).get(m, DASH),
                         ", ".join(f"{f} ({v / total * 100:.0f} %)" for f, v in top)])
    out = ["## Feature importance (top 5 by gain share)",
           "Materials served by the shared model show the same ranking (one model).",
           _table(["material", "split", "model", "top features"], rows)]
    for split in splits_of(metrics):
        out += [_img(models_dir, f"feature_importance_{split}", f"feature importance {split}"),
                _img(models_dir, f"shap_{split}", f"SHAP summary {split}")]
    return "\n\n".join(filter(None, out))


# ---------------------------------------------------------------- target check
def _outlier_facts(m: dict, g: pd.DataFrame | None, n_machines: int | str) -> list[str]:
    facts = []
    mean, median = metric(m, "mean_pct"), metric(m, "median_pct")
    if m.get("n", 0) < SMALL_N_LIFTS:
        facts.append(f"only {m.get('n', 0)} test lifts on {n_machines} test machine(s), so a few cycles dominate")
    if median < TARGET_PCT and mean > OUTLIER_RATIO * median:
        facts.append(f"the median is {_num(median)} %, so a few large misses pull the mean up")
    elif median >= OUTLIER_RATIO_BROAD * TARGET_PCT:
        facts.append(f"the error is broad-based (median {_num(median)} %), not driven by a few outliers")
    if g is None or g["pct"].notna().sum() < 3:
        return facts
    err = g["pct"].dropna().sort_values(ascending=False)
    if err.iloc[:3].sum() / err.sum() >= WORST3_SHARE:
        facts.append(f"the 3 worst lifts carry {_pct(err.iloc[:3].sum() / err.sum())} of the summed % error")
    ff, clean = g[g["false_full_in_cycle"]]["pct"].dropna(), g[~g["false_full_in_cycle"]]["pct"].dropna()
    if len(ff) and len(clean) and ff.mean() > clean.mean():
        facts.append(f"{len(ff)} of {len(g)} lifts had a false-full episode in the cycle "
                     f"({_num(ff.mean())} % vs {_num(clean.mean())} % without)")
    return facts


def _context_facts(split: str, sp: dict, mat: str, n_machines: int | str, fleet: dict[str, int]) -> list[str]:
    facts = []
    if fleet.get(mat) is not None:
        trained = (f", at most {fleet[mat] - n_machines} of them in training"
                   if split == "bins" and isinstance(n_machines, int) else "")
        facts.append(f"the fleet has {fleet[mat]} {mat} machine(s) of {sum(fleet.values())}{trained}")
    val = sp.get("training", {}).get("val_lift_mean_pct", {}).get(mat, {})
    if val and all(np.isnan(metric(val, k)) for k in ("shared", "per_material")):
        facts.append("no validation lifts for this material, so its setup choice was not validated")
    other = "per_material" if sp.get("selection", {}).get(mat) == "shared" else "shared"
    alt = _ladder_value(sp, f"full_{other}", mat)
    if alt < TARGET_PCT:
        facts.append(f"the {other.replace('_', '-')} model scores {_num(alt)} % on test "
                     "(validation chose the other setup)")
    press = _ladder_value(sp, "pressure_graph", mat)
    if not np.isfinite(press):
        press = _ladder_value(sp, "pressure", mat)
    weather = _ladder_value(sp, "full_shared", mat)
    if weather > press + 0.2:
        facts.append(f"adding weather did not help ({_num(press)} % with pressure → {_num(weather)} % full shared)")
    bias = metric(_block(sp, "lift", mat), "bias_pct")
    if abs(bias) > BIAS_FLAG_PCT:
        facts.append(f"systematic bias of {bias:+.1f} %")
    return facts


def _reasons(split: str, sp: dict, mat: str, lift: pd.DataFrame | None, fleet: dict[str, int]) -> list[str]:
    g = lift[lift["material"] == mat] if lift is not None else None
    n_machines = int(g["bin_id"].nunique()) if g is not None else DASH
    return _outlier_facts(_block(sp, "lift", mat), g, n_machines) + _context_facts(split, sp, mat, n_machines, fleet)


def _target_section(models_dir: Path, metrics: dict, mats: list[str]) -> str:
    fleet = _fleet_machines(models_dir)
    rows, misses = [], []
    for split in splits_of(metrics):
        sp = metrics["splits"][split]
        lift = _lift_frame(models_dir, split)
        for mat in mats:
            m = _block(sp, "lift", mat)
            if not m:
                continue
            value = metric(m, "mean_pct")
            verdict = "n/a" if np.isnan(value) else ("yes" if value < TARGET_PCT else "**no**")
            rows.append([mat, split, _num(value), str(m.get("n", DASH)), verdict])
            if verdict == "**no**":
                misses.append(f"- **{mat}, `{split}` split ({_num(value)} %)**: "
                              + ("; ".join(_reasons(split, sp, mat, lift, fleet)) or "no single cause visible") + ".")
    body = _table(["material", "split", "mean % at lift", "n lifts", f"< {TARGET_PCT:.0f} %"], rows)
    tail = "\n".join(misses) if misses else "All materials meet the target on every split."
    return (f"## Target check (mean % error at lift < {TARGET_PCT:.0f} %)\n\n{body}\n\n"
            + ("**Not met, and the likely reasons from the data:**\n\n" if misses else "") + tail)


# ---------------------------------------------------------------- entry point
def write_report(models_dir: Path) -> Path:
    models_dir = Path(models_dir)
    metrics = load_metrics(models_dir)
    mats = materials_of(metrics)
    curve_split = "time" if "time" in splits_of(metrics) else next(iter(splits_of(metrics)), None)
    sections = [
        f"# Hydraulic soft sensor - container mass report (`{models_dir.name}`)",
        _summary(metrics, mats),
        _img(models_dir, "lift_error_by_material", "mean % error at lift by material"),
        _compute(models_dir, metrics),
        *[_split_section(models_dir, s, metrics["splits"][s], mats) for s in splits_of(metrics)],
        _ladder_section(models_dir, metrics, mats),
        _setup_section(metrics, mats),
        _coverage_section(models_dir, metrics, mats),
        ("## Hourly mass curves\n\n" + _img(models_dir, f"mass_curves_{curve_split}", "hourly mass curves"))
        if curve_split and _img(models_dir, f"mass_curves_{curve_split}", "") else "",
        _breakdown_section(metrics),
        _learning_section(models_dir, metrics, mats),
        _importance_section(models_dir, metrics, mats),
        _target_section(models_dir, metrics, mats),
    ]
    path = models_dir / "REPORT.md"
    path.write_text("\n\n".join(s for s in sections if s) + "\n")
    log.info("wrote %s", path)
    return path
