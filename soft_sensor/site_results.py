"""Section 3: results and evaluation of the soft sensor (per split, ladder, intervals, breakdowns, learning curve)."""
from __future__ import annotations

from html import escape
from pathlib import Path

from site_common import (GOOD_PCT, SPLIT_LABEL, Build, dig, figures, h2, h3, load_csv, load_json, note, num, p, pct_of,
                         raw_table, table, term)
from site_glossary import LADDER_STEPS

MATERIALS = ("cardboard", "paper", "plastic_film", "mixed_waste")
LIFT_HEAD = ["material", "lifts", "MAE kg", "mean %", "median %", "within 5 % (%)", "within 10 % (%)", "bias kg",
             "coverage %", "width kg"]
BREAKDOWN_LABEL = {"infeed": "infeed type", "drainage": "drainage (0 = no, 1 = yes)", "equipment_class": "equipment class",
                   "container_size": "container size", "false_full_in_cycle": "false-full episode in the cycle",
                   "week": "rain in the week before the lift"}


def lift_row(name: str, g: dict) -> list[str]:
    return [name, num(g.get("n"), 0), num(g.get("mae_kg"), 0), num(g.get("mean_pct")), num(g.get("median_pct")),
            pct_of(g.get("within_5pct"), 0), pct_of(g.get("within_10pct"), 0), num(g.get("bias_kg"), 0),
            pct_of(g.get("coverage"), 0), num(g.get("width_kg"), 0)]


def _mats(sp: dict) -> list[str]:
    bym = dig(sp, "final", "lift", "by_material") or {}
    return [m for m in MATERIALS if m in bym] + [m for m in bym if m not in MATERIALS]


def pick(pngs: dict[str, Path], *stems: str) -> list[Path]:
    return [pngs[s] for s in stems if s in pngs]


# ---------------------------------------------------------------- 3.1 per split
def split_results(metrics: dict | None, pngs: dict[str, Path]) -> str:
    out = [h3("Error at lift and per hour, by split and material", "res-split"),
           p("The selected model per material (Section 3.3). The first table of each split is the weighbridge comparison "
             "(pre-lift hour); the second uses every hour of the test period, where the container is often far from full.")]
    for split in ("time", "bins"):
        sp = dig(metrics, "splits", split)
        if not sp:
            continue
        sel = ", ".join(f"{m} → {s.replace('_', '-')}" for m, s in (sp.get("selection") or {}).items())
        lift = dig(sp, "final", "lift") or {}
        rows = [lift_row(m, lift["by_material"][m]) for m in _mats(sp)] + [lift_row("overall", lift.get("overall") or {})]
        hourly = dig(sp, "final", "hourly") or {}
        hrows = [[m, num(g.get("mae_kg"), 0), num(g.get("mean_pct")), num(g.get("median_pct")), pct_of(g.get("coverage"), 0),
                  num(g.get("width_kg"), 0)] for m, g in [*((m, hourly["by_material"][m]) for m in _mats(sp)),
                                                           ("overall", hourly.get("overall") or {})]]
        out.append(f"<h4>{escape(SPLIT_LABEL[split])}</h4>"
                   + p(f"{escape(sp.get('description', ''))}. Selected setup: {escape(sel)}.")
                   + table(LIFT_HEAD, rows, good_cols={3, 4}, caption=f"At lift, {SPLIT_LABEL[split]}: estimate in the pre-lift "
                           "hour versus true mass.")
                   + table(["material", "MAE kg", "mean %", "median %", "coverage %", "width kg"], hrows, good_cols={2, 3},
                           caption=f"Every test hour, {SPLIT_LABEL[split]}."))
    out.append(figures(pick(pngs, "pred_vs_true_time", "pred_vs_true_bins")))
    return "".join(out)


# ---------------------------------------------------------------- 3.2 ladder
def ladder(metrics: dict | None, pngs: dict[str, Path]) -> str:
    out = [h3("Comparison ladder", "res-ladder"),
           p("Mean % error at lift on the same test lifts when one signal family is added at a time (step definitions in "
             "Section 1.5, feature sets in Section 2.3). The largest single gain is from the fill reading alone to adding the "
             "press counts and then the peak pressure; the pressure graph and weather add a few tenths of a percentage point.")]
    for split in ("time", "bins"):
        lad = dig(metrics, "splits", split, "ladder") or {}
        if not lad:
            continue
        mats = [m for m in MATERIALS if any(m in (st.get("by_material") or {}) for st in lad.values())]
        rows = []
        for step in LADDER_STEPS:
            key = step.replace(" (ladder step)", "")
            if key not in lad:
                continue
            bym = lad[key].get("by_material") or {}
            rows.append([term(step, key)] + [escape(num(dig(bym, m, "mean_pct"))) for m in mats]
                        + [f"<strong>{escape(num(dig(lad[key], 'overall', 'mean_pct')))}</strong>"])
        out.append(f"<h4>{escape(SPLIT_LABEL[split])}</h4>"
                   + raw_table(["step"] + mats + ["overall"], rows, caption=f"Comparison ladder, {SPLIT_LABEL[split]} (mean % error at lift)."))
    out.append(figures(pick(pngs, "ladder"), single=True))
    return "".join(out) if len(out) > 2 else out[0] + note("No ladder results available.")


# ---------------------------------------------------------------- 3.3 shared vs per material
def selection(metrics: dict | None) -> str:
    rows = []
    for split in ("time", "bins"):
        sp = dig(metrics, "splits", split) or {}
        val = dig(sp, "training", "val_lift_mean_pct") or {}
        for m in _mats(sp):
            vs, vp = dig(val, m, "shared"), dig(val, m, "per_material")
            ts = dig(sp, "ladder", "full_shared", "by_material", m, "mean_pct")
            tp = dig(sp, "ladder", "full_per_material", "by_material", m, "mean_pct")
            chosen = (sp.get("selection") or {}).get(m, "–")
            better = "–" if ts is None or tp is None else ("per_material" if tp < ts else "shared")
            rows.append([m, SPLIT_LABEL[split], num(vs), num(vp), num(ts), num(tp), chosen.replace("_", "-"), better.replace("_", "-")])
    return (h3("Shared versus per-material model", "res-selection")
            + p("For each material the setup is chosen on the validation lifts only (mean % error). The test columns are "
                "shown for reference; the choice never looks at them. Monotone constraints on fill_ffill and presses_cycle "
                "are kept per model when they cost less than 2 % validation error.")
            + table(["material", "split", "validation: shared", "validation: per-material", "test: shared", "test: per-material",
                     "selected", "better on test"], rows, good_cols={2, 3, 4, 5}, left_cols={0, 1, 6, 7},
                    caption="Validation and test mean % error at lift of the shared and the per-material model."))


# ---------------------------------------------------------------- 3.4 intervals
def intervals(metrics: dict | None, pngs: dict[str, Path]) -> str:
    rows, low = [], []
    for split in ("time", "bins"):
        sp = dig(metrics, "splits", split) or {}
        for m in _mats(sp) + ["overall"]:
            lift = dig(sp, "final", "lift", "by_material", m) if m != "overall" else dig(sp, "final", "lift", "overall")
            hour = dig(sp, "final", "hourly", "by_material", m) if m != "overall" else dig(sp, "final", "hourly", "overall")
            lift, hour = lift or {}, hour or {}
            rows.append([m, SPLIT_LABEL[split], pct_of(lift.get("coverage"), 0), num(lift.get("width_kg"), 0),
                         pct_of(hour.get("coverage"), 0), num(hour.get("width_kg"), 0)])
            cov = lift.get("coverage")
            if m != "overall" and cov is not None and cov < 0.75:
                low.append(f"{m} on {SPLIT_LABEL[split]} ({pct_of(cov, 0)} %)")
    target = pct_of(dig(metrics, "settings", "target_coverage"), 0)
    edges = dig(metrics, "settings", "fill_buckets") or []
    inner = ", ".join(num(e, 0) for e in edges[1:-1])
    text = (p(f"Intervals come from LightGBM quantile models for p10 and p90, widened by conformal calibration (CQR) on the "
              f"calibration slice to a nominal coverage of {target} %. Calibration is Mondrian by material × fill bucket "
              f"(bucket edges at {inner} % fill); strata with fewer than {dig(metrics, 'settings', 'min_cal_points')} "
              "calibration points fall back to the material and then to the pool. In the top fill bucket the larger of the "
              "all-hours and the pre-lift-hours quantile is used, so that the weighbridge hour is covered too."))
    if low:
        text += p("Lift coverage more than 5 pp below target: " + escape("; ".join(low)) + ". The calibration machines of the "
                  "unseen-machines split are few for the smaller materials, so their error scale need not transfer to the test "
                  "machines; the lift hour is also the fullest, hardest hour of a cycle. See Section 6.")
    return (h3("Prediction intervals and coverage", "res-intervals") + text
            + table(["material", "split", "lift coverage %", "lift width kg", "hourly coverage %", "hourly width kg"], rows,
                    left_cols={0, 1}, caption=f"Coverage and mean width of the calibrated p10–p90 interval (target {target} %).")
            + figures(pick(pngs, "coverage_by_material"), single=True))


# ---------------------------------------------------------------- 3.5 breakdowns
def breakdowns(metrics: dict | None) -> str:
    t, u = (dig(metrics, "splits", s, "breakdowns") or {} for s in ("time", "bins"))
    rows, classes = [], []
    for key, label in BREAKDOWN_LABEL.items():
        groups = list(dict.fromkeys([*(t.get(key) or {}), *(u.get(key) or {})]))
        if not groups:
            continue
        rows.append([escape(label), "", "", "", ""])
        classes.append("fam")
        for g in groups:
            a, c = (t.get(key) or {}).get(g) or {}, (u.get(key) or {}).get(g) or {}
            rows.append([escape(str(g).replace("_", " ")), escape(num(a.get("mean_pct"))), escape(num(a.get("n"), 0)),
                         escape(num(c.get("mean_pct"))), escape(num(c.get("n"), 0))])
            classes.append("")
    return (h3("Error breakdowns", "res-breakdowns")
            + p("Mean % error at lift of the selected model by machine and situation. Cycles with a false-full episode are "
                "clearly harder; open hoppers and DUO units are somewhat harder on unseen machines but not on known ones; "
                "rain in the week before the lift changes little, i.e. the weather features absorb most of the moisture effect.")
            + raw_table(["group", "same machines, later months: mean %", "n", "unseen machines: mean %", "n"], rows,
                        row_class=classes, caption="Error at lift by infeed, drainage, equipment class, container size, "
                        "false-full episodes and recent rain."))


# ---------------------------------------------------------------- 3.6 learning curve
def learning(b: Build, model_run: Path, metrics: dict | None, pngs: dict[str, Path]) -> str:
    sp = dig(metrics, "splits", "bins") or {}
    df = load_csv(b, model_run / "learning_curve.csv")
    if df is None or not {"n_machines", "material", "mean_pct"} <= set(df.columns):
        return h3("Learning curve", "res-learning") + note("No learning curve available.")
    piv = df.pivot_table(index="n_machines", columns="material", values="mean_pct", aggfunc="first")
    mats = [m for m in MATERIALS if m in piv.columns]
    repeats = {x["n_machines"]: x.get("repeats") for x in sp.get("learning_curve") or []}
    n_all = sp.get("n_train_machines")
    rows = [[f"{n} (all)" if n == n_all else str(n), str(repeats.get(n, 1)), num(piv.at[n, "all"]) if "all" in piv.columns else "–"]
            + [num(piv.at[n, m]) for m in mats] for n in piv.index]
    vals = [(int(n), float(piv.at[n, "all"])) for n in piv.index] if "all" in piv.columns else []
    ref = vals[-1][1] if vals else None
    near = next((n for n, v in vals if ref is not None and v <= ref + 0.5), None)
    text = p(f"Shared model trained on random subsets of the {n_all or '–'} training machines of the unseen-machines split and "
             f"tested on its {sp.get('n_test_machines', '–')} test machines. Small sizes are repeated with different machine draws. "
             + (f"The smallest size within 0.5 pp of the all-machines value ({num(ref)} %) is {near} machines; "
                f"the largest gain is from {vals[0][0]} to {vals[1][0]} machines ({num(vals[0][1])} → {num(vals[1][1])} %)."
                if near and len(vals) > 1 else "")
             + " Section 4 repeats this for a single material with finer steps.")
    return (h3("Learning curve", "res-learning") + text
            + table(["training machines", "repeats", "overall"] + mats, rows, good_cols=set(range(2, len(mats) + 3)),
                    caption="Mean % error at lift on unseen machines versus number of training machines (shared model).")
            + figures(pick(pngs, "learning_curve"), single=True))


# ---------------------------------------------------------------- 3.7–3.9 curves, target, compute, early baseline
def curves_and_target(metrics: dict | None, pngs: dict[str, Path]) -> str:
    rows = []
    for split in ("time", "bins"):
        sp = dig(metrics, "splits", split) or {}
        for m in _mats(sp):
            g = dig(sp, "final", "lift", "by_material", m) or {}
            ok = g.get("mean_pct") is not None and g["mean_pct"] < GOOD_PCT
            rows.append([m, SPLIT_LABEL[split], num(g.get("mean_pct")), num(g.get("n"), 0), "yes" if ok else "no"])
    return (h3("Hourly mass curves", "res-curves")
            + p("The hourly estimate follows the container through each cycle: it rises with loading, is reset by the lift "
                "and carries its interval along. Example machines from the test period of the same-machines split.")
            + figures(pick(pngs, "mass_curves_time"), single=True)
            + h3("Target check", "res-target")
            + p(f"The hackathon target is a mean error below {GOOD_PCT:.0f} % at lift. It is met for every material when the "
                "machines are known, and for plastic film only when they are not; the misses on unseen machines are broad-based "
                "(the medians are close to the means) rather than caused by a few outliers.")
            + table(["material", "split", "mean % at lift", "lifts", f"below {GOOD_PCT:.0f} %"], rows, good_cols={2}, left_cols={0, 1, 4},
                    caption="Target check per material and split."))


def compute(metrics: dict | None, stats: dict | None) -> str:
    c = (metrics or {}).get("compute") or {}
    rows = [["training wall time, both splits", f"{num(c.get('train_wall_s'), 0)} s"],
            ["feature build", f"{num(c.get('feature_build_s'))} s"], ["peak memory (training)", f"{num(c.get('peak_rss_gb'), 2)} GB"],
            ["hourly rows / machines", f"{num(c.get('rows'), 0)} / {c.get('machines', '–')}"]]
    for s in ("time", "bins"):
        rows.append([f"training wall time, {SPLIT_LABEL[s]}", f"{num(dig(metrics, 'splits', s, 'train_wall_s'), 0)} s"])
    if stats:
        rows += [["prediction run (features + inference)", f"{num(stats.get('rows'), 0)} rows in {num(stats.get('wall_s'))} s"],
                 ["prediction cost", f"{num(stats.get('us_per_machine_hour'))} µs per machine-hour"],
                 ["peak memory (prediction)", f"{num(stats.get('peak_rss_gb'), 2)} GB"]]
    return (h3("Compute", "res-compute")
            + p("CPU only, deterministic (fixed seeds). Same data and seed give identical metrics.")
            + table(["item", "value"], rows, left_cols={0, 1}, caption="Training and inference cost."))


def early_baseline(b: Build, sim_run: Path) -> str:
    data = load_json(b, sim_run / "baseline_results.json")
    if not data:
        return ""
    rows = [[k, num(v.get("mean_ape_pct")), num(v.get("median_ape_pct")), num(v.get("p90_ape_pct")), num(v.get("wape_pct")),
             num(v.get("mae_kg"), 0)] for k, v in data.items() if isinstance(v, dict) and "mean_ape_pct" in v]
    return (h3("Early baseline (different protocol)", "res-baseline")
            + p("Before the full pipeline existed, a plain LightGBM model of the lift mass was checked with 5-fold "
                "cross-validation grouped by machine on all lifts of at least 200 kg, adding one signal group at a time "
                "(no intervals, no pressure graph, no per-material models, no time split). It is kept here because it "
                "shows the same ordering with an independent protocol: the fill reading alone is weak, peak pressure and "
                "press counts carry most of the information, weather adds little on average.")
            + table(["features", "mean %", "median %", "p90 error %", "WAPE %", "MAE kg"], rows, good_cols={1},
                    caption="Early baseline, 5-fold cross-validation grouped by machine."))


def results(b: Build, metrics: dict | None, model_run: Path, sim_run: Path, pngs: dict[str, Path]) -> str:
    stats = load_json(b, model_run / "predict_stats.json")
    head = '<section id="sec-results">' + h2("Results and evaluation", "results")   # numbering: heading first
    body = (split_results(metrics, pngs) + ladder(metrics, pngs) + selection(metrics) + intervals(metrics, pngs)
            + breakdowns(metrics) + learning(b, model_run, metrics, pngs) + curves_and_target(metrics, pngs) + compute(metrics, stats)
            + early_baseline(b, sim_run)) if metrics else note("No evaluation metrics available at build time.")
    return head + body + "</section>"
