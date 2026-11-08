"""Section 2: model inputs and features (raw inputs, exclusions, feature table, pressure inversion, importance, example)."""
from __future__ import annotations

import logging
import re
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

from site_common import (ROOT, SPLIT_LABEL, Build, dig, figures, h2, h3, h4, note, num, p, raw_table, table)
from site_glossary import COLUMNS, TRUTH
from weight_train.columns import CURVE_COLUMNS, DEVICE_COLUMNS, FORBIDDEN_COLUMNS, FORBIDDEN_PREFIXES, OPTIONAL_DEVICE_COLUMNS
from weight_train.features import (CALENDAR, CYCLE, FEATURE_SETS, FILL, GRAPH, MACHINE, PRESSES, PRESSURE, STROKES,
                                   WEATHER)
from weight_train.settings import (MIN_FILL_FOR_PRESSURE, NOMINAL_DENSITY, P_IDLE_NOMINAL_BAR, RELIEF_GUARD_BAR,
                                   VISC_COEFF_PER_C, VISC_REF_C)

FAMILIES = (("machine", MACHINE), ("calendar", CALENDAR), ("cycle", CYCLE), ("fill", FILL), ("presses", PRESSES),
            ("peak pressure", PRESSURE), ("strokes", STROKES), ("pressure graph", GRAPH), ("weather", WEATHER))
FORBIDDEN_WHY = {
    "lifted_mass_kg": "mass removed by the lift (the answer)", "side_waste_kg": "overflow left outside (truth)",
    "ref_scale_kg": "load-cell reading on about 5 % of machines (a scale: the thing the soft sensor replaces)",
    "density_factor": "hidden per-machine density multiplier", "daily_input_kg": "hidden site input rate",
    "target_cycle_days": "hidden site cycle length", "p_idle_bar": "true idle pressure of the machine",
    "k_machine": "true hydraulic scale of the machine", "relief_bar": "true relief-valve setting",
    "fill_gain_bias": "fill sensor bias", "pressure_gain_bias": "pressure transducer bias",
    "pressure_gain_drift_per_year": "pressure transducer drift", "storage_outdoor_share": "hidden storage habit",
    "threshold_pct": "order threshold of the site",
}
EXAMPLE_HOURS = 8


def feature_docs() -> dict[str, str]:
    """feature -> meaning, parsed from the features table of the training-pipeline README."""
    path = ROOT / "weight_train" / "README.md"
    docs: dict[str, str] = {}
    if not path.exists():
        return docs
    for line in path.read_text().splitlines():
        m = re.match(r"\|\s*(`[^|]+)\|\s*(.+?)\s*\|\s*$", line)
        if not m:
            continue
        names = re.findall(r"`([^`]+)`", m.group(1))
        if "…" in m.group(1) and len(names) == 2:   # rain_mm_d1 … rain_mm_d7
            stem = names[0].rstrip("0123456789")
            names = [f"{stem}{k}" for k in range(int(names[0][len(stem):]), int(names[1][len(stem):]) + 1)]
        for n in names:
            docs[n] = m.group(2).replace("`", "").replace("(from strokes.parquet)", "(from the per-stroke log)")
    dens = "/".join(num(x, 0) for x in NOMINAL_DENSITY.values())   # keep in sync with the public constants
    return {k: re.sub(r"\(\d+(?:/\d+)+ kg/m³", f"({dens} kg/m³", d) for k, d in docs.items()}


# ---------------------------------------------------------------- 2.1 / 2.2 inputs
def raw_inputs() -> str:
    rows = []
    for col in list(DEVICE_COLUMNS) + list(OPTIONAL_DEVICE_COLUMNS):
        meaning, origin = COLUMNS.get(col, ("–", "–"))
        kind = "required" if col in DEVICE_COLUMNS else "optional"
        rows.append([f"<code>{escape(col)}</code>", escape(meaning), escape(origin), kind])
    rows.append(["per-stroke log", escape("peak pressure and duration of every stroke; only hourly means are used"),
                 escape("machine telemetry"), "optional"])
    return (h3("Raw inputs", "in-raw")
            + p("Only the columns below are read: they form a fixed whitelist, and every other column of the data is "
                "dropped before any feature is computed. The pressure-graph columns and the per-stroke log are optional; "
                "without them the corresponding features are empty and the model falls back on the remaining signals.")
            + raw_table(["column", "meaning", "origin", "status"], rows, left_cols={0, 1, 2, 3},
                        caption="Hourly input columns the soft sensor may read.")
            + p("<strong>Today's daily precipitation and relative humidity are not used for the current day</strong>: a daily "
                "total or mean would contain hours that have not happened yet. Daily weather enters only as completed days "
                "(yesterday and earlier); the hourly precipitation is used only up to the current hour."))


def excluded(data_run: Path) -> str:
    true_cols: list[str] = []
    try:
        import pyarrow.parquet as pq
        true_cols = [c for c in pq.read_schema(ROOT / data_run / "hourly.parquet").names if c.startswith(FORBIDDEN_PREFIXES)]
    except (OSError, ValueError) as exc:
        logging.warning("cannot read hourly schema: %s", exc)
    rows = [[f"<code>{escape(c)}</code>", escape(FORBIDDEN_WHY.get(c, "hidden truth"))] for c in sorted(FORBIDDEN_COLUMNS)]
    truth = [[f"<code>{escape(c)}</code>", escape(TRUTH.get(c, "simulator truth"))] for c in true_cols]
    return (h3("Excluded inputs (leakage control)", "in-excluded")
            + p("The simulator also writes the truth. None of it may reach the model, or the test numbers would be "
                "meaningless. Two groups are excluded: every column whose name starts with <code>true_</code>, and the "
                "columns below, which are truth even though their names do not say so.")
            + raw_table(["truth column in the hourly data", "what it is"], truth, left_cols={0, 1},
                        caption="Truth columns of the hourly data (never inputs).")
            + raw_table(["column", "what it is"], rows, left_cols={0, 1}, caption="Further forbidden columns (hidden machine and site truth).")
            + p("A leakage guard rejects any such column at feature-build and fit time, and the automated tests enforce it: "
                "no truth column reaches the feature matrix, planted truth columns are rejected, scrambling the truth changes "
                "nothing, cutting or scrambling the future leaves earlier features identical, and daily weather lags use "
                "completed days only."))


# ---------------------------------------------------------------- 2.3 features, 2.4 inversion
def feature_table(docs: dict[str, str]) -> str:
    sets = list(FEATURE_SETS)
    rows, classes = [], []
    for fam, cols in FAMILIES:
        rows.append([f"{escape(fam)} ({len(cols)})"] + [""] * (len(sets) + 1))
        classes.append("fam")
        for c in cols:
            rows.append([f"<code>{escape(c)}</code>", escape(docs.get(c, "–"))] + ["✓" if c in FEATURE_SETS[s] else "" for s in sets])
            classes.append("")
    rows.append(["<code>material</code>", escape(docs.get("material", "material (shared model only)"))] + ["shared"] * len(sets))
    classes.append("")
    head = ["feature", "meaning"] + [f"<code>{escape(s)}</code>" for s in sets]
    nominal = ", ".join(f"{k} {num(x, 0)}" for k, x in NOMINAL_DENSITY.items())
    return (h3("Engineered features", "in-features")
            + p("All features are computed per machine and hour from rows up to and including the current hour; a cycle "
                "starts at each lift event. Smoothed values use an EWMA restarted at every lift. Nominal densities used by "
                f"naive_kg_m3 and graph_kg_m3 (public values, kg/m³): {escape(nominal)}. Ticks show which comparison-ladder "
                "feature set contains the feature; month is deliberately not a feature because the test months are unseen.")
            + raw_table(head, rows, left_cols={0, 1}, row_class=classes, caption="Feature families and the ladder feature sets."))


def inversion(metrics: dict | None) -> str:
    rows = []
    for split in ("time", "bins"):
        inv = dig(metrics, "splits", split, "inverter") or {}
        for m, (log_k, a, b_) in inv.items():
            rows.append([m, SPLIT_LABEL[split], num(np.exp(log_k), 2), num(a, 2), num(b_, 2)])
    return (h3("Pressure-implied density", "in-inversion")
            + p("The feature rho_p turns a stroke's peak pressure into a pack density by inverting a simple pressure law "
                "fitted on training rows only. The density used for fitting is derived from the training label (kg/m³ "
                "divided by the fill fraction), never from simulator truth columns.")
            + f"<pre><code>P / visc(T_oil) − {P_IDLE_NOMINAL_BAR:.0f} bar = k · (fill/100)^a · ρ^b          (per material; log-linear least squares)\n"
              f"visc(T_oil) = 1 + {VISC_COEFF_PER_C} · max({VISC_REF_C:.0f} − T_oil, 0)\n"
              f"used only when fill &gt; {MIN_FILL_FOR_PRESSURE:.0f} % and P &lt; {RELIEF_GUARD_BAR:.0f} bar (below the relief valve), ρ clipped to 20–900 kg/m³\n"
              "rho_p_smooth = EWMA of rho_p within the cycle (half-life 4);  pressure_kg_m3 = fill/100 × rho_p_smooth</code></pre>"
            + p("The fitted exponent b is close to 1 for every material, i.e. the pressure depends about linearly on density "
                "— the 'medium' dependence built into the simulator — and the fill exponent a is near 1 as well.")
            + table(["material", "split", "k (bar)", "a (fill exponent)", "b (density exponent)"], rows, left_cols={0, 1},
                    caption="Fitted coefficients of the pressure law per material and split."))


# ---------------------------------------------------------------- 2.5 importance
def importance(metrics: dict | None, pngs: dict[str, Path]) -> str:
    parts = []
    for split in ("time", "bins"):
        sp = dig(metrics, "splits", split) or {}
        groups: dict[tuple, list[str]] = {}
        for mat, gains in (sp.get("feature_importance_gain") or {}).items():
            key = (dig(sp, "selection", mat) or "?", tuple(sorted(gains.items())))
            groups.setdefault(key, []).append(mat)
        cards = []
        for (setup, items), mats in groups.items():
            gains = dict(items)
            total = sum(gains.values()) or 1.0
            top = sorted(gains.items(), key=lambda kv: -kv[1])[:10]
            bars = "".join(f'<div class="bar"><code>{escape(f)}</code><div class="track"><div class="fill" '
                           f'style="width:{100 * g / top[0][1]:.1f}%"></div></div><span>{100 * g / total:.1f} %</span></div>' for f, g in top)
            cards.append(f'<div class="bar-card"><h5>{escape(" · ".join(mats))} <span class="muted">({escape(setup.replace("_", "-"))} '
                         f"model)</span></h5>{bars}</div>")
        if cards:
            parts.append(h4(SPLIT_LABEL[split]) + f'<div class="bars">{"".join(cards)}</div>')
    figs = [pngs[s] for s in ("feature_importance_time", "shap_time", "feature_importance_bins", "shap_bins") if s in pngs]
    return (h3("Feature importance", "in-importance")
            + p("Share of the total split gain of the model each material actually uses (top 10). Materials served by the same "
                "shared model share one chart. The smoothed peak pressure p_ewm and the rise slope of the pressure graph "
                "dominate; the physics-based pressure_kg_m3 and the press count follow. The SHAP plots show the direction "
                "of each feature's effect on the pre-lift hours.")
            + ("".join(parts) or note("No importance values available."))
            + figures(figs))


# ---------------------------------------------------------------- 2.6 example
def _pick_lift(h: pd.DataFrame, test_start: pd.Timestamp | None) -> tuple[str, pd.Timestamp] | None:
    busy = h.groupby("bin_id", sort=False)["presses"].transform(lambda x: x.shift(1).rolling(EXAMPLE_HOURS - 1).sum())
    lifts = h[(h["lift_event"] == 1) & (h["true_mass_kg"].shift(1) > 0) & (busy >= 3) & (h["ts"].dt.hour >= 12)]
    if test_start is not None:
        lifts = lifts[lifts["ts"] >= test_start]
    for mat in ("cardboard", "paper", "plastic_film", "mixed_waste"):
        cand = lifts[lifts["material"] == mat]
        if len(cand) > 2:
            row = cand.iloc[len(cand) // 2]
            return str(row["bin_id"]), row["ts"]
    return None


def example(model_run: Path, data_run: Path, metrics: dict | None) -> str:
    from weight_train.bundle import SoftSensor
    from weight_train.features import build_features
    head = h3("Worked example: one machine before a lift", "in-example")
    split = next((s for s in ("time", "bins") if (ROOT / model_run / s / "bundle.json").exists()), None)
    hpath = ROOT / data_run / "hourly.parquet"
    if split is None or not hpath.exists():
        return head + note("No trained model bundle or matching hourly data for the example.")
    try:
        sensor = SoftSensor.load(ROOT / model_run / split)
        h = pd.read_parquet(hpath)
        ts0 = dig(metrics, "splits", split, "test_start")
        pick = _pick_lift(h, pd.Timestamp(ts0) if ts0 else None)
        if pick is None:
            return head + note("No suitable lift found for the example.")
        bin_id, lift_ts = pick
        one = h[h["bin_id"] == bin_id].reset_index(drop=True)
        spath = ROOT / data_run / "strokes.parquet"
        strokes = (pd.read_parquet(spath, columns=["bin_id", "ts", "peak_pressure_bar", "duration_s"],
                                   filters=[("bin_id", "==", bin_id)]) if spath.exists() else None)
        feats = build_features(one, sensor.inverter, strokes)
        pred = sensor.predict_frame(feats)
    except (OSError, ValueError, KeyError) as exc:
        logging.warning("example failed: %s", exc)
        return head + note("The worked example could not be built from the available files.")
    pos = int(np.flatnonzero((feats.meta["ts"] == lift_ts).to_numpy())[0])
    rows_idx = list(range(pos - EXAMPLE_HOURS + 1, pos + 1))
    X = feats.X.iloc[rows_idx]
    truth = one.set_index("ts").loc[feats.meta["ts"].iloc[rows_idx], "true_mass_kg"].to_numpy()
    hours = [t.strftime("%a %H:%M") for t in feats.meta["ts"].iloc[rows_idx]]
    hc = [""] + ["lift" if i >= EXAMPLE_HOURS - 2 else "" for i in range(EXAMPLE_HOURS)]
    rows, classes = [], []

    def add(label: str, vals, cls: str = "", nd: int = 2) -> None:
        nums = [float(x) for x in vals if isinstance(x, (int, float, np.floating, np.integer)) and not pd.isna(x)]
        if nums and all(x.is_integer() for x in nums):
            nd = 0
        cells = [escape(str(x)) if not isinstance(x, (int, float, np.floating, np.integer))
                 else ("–" if pd.isna(x) else escape(num(x, nd))) for x in vals]
        rows.append([label] + cells)
        classes.append(cls)

    pr = pred.iloc[rows_idx]
    add("<strong>true_mass_kg</strong> (truth, not an input)", truth, "key", 0)
    add("<strong>mass_pred_kg</strong>", pr["mass_pred_kg"].to_numpy(), "key", 0)
    add("mass_p10_kg … mass_p90_kg", [f"{num(a, 0)} … {num(c, 0)}" for a, c in zip(pr["mass_p10_kg"], pr["mass_p90_kg"])])
    for fam, cols in FAMILIES:
        rows.append([escape(fam)] + [""] * EXAMPLE_HOURS)
        classes.append("fam")
        for c in cols:
            if c in X:
                add(f"<code>{escape(c)}</code>", X[c].tolist())
    mat = str(feats.meta["material"].iloc[pos])
    vol = num(feats.meta["container_volume_m3"].iloc[pos], 0)
    return (head
            + p(f"One {escape(mat)} machine ({escape(vol)} m³) in the test period of the {escape(SPLIT_LABEL[split])} split, "
                f"so these hours were not trained on: the {EXAMPLE_HOURS} hours up to the lift at "
                f"{escape(lift_ts.strftime('%Y-%m-%d %H:%M'))}. Features are exactly those the deployed model sees; the "
                "prediction is that of the selected model. The two shaded columns are the pre-lift hour (the value compared "
                "with the weighbridge) and the lift hour itself (container emptied at its start, so the true mass is only that "
                "hour's new material).")
            + raw_table(["feature"] + [escape(x) for x in hours], rows, left_cols={0}, row_class=classes, col_class=hc,
                        caption=f"Feature values and predictions for the {EXAMPLE_HOURS} hours before one lift."))


def model_inputs(b: Build, metrics: dict | None, model_run: Path, data_run: Path, docs: dict[str, str], pngs: dict[str, Path]) -> str:
    b.check(Path("weight_train/README.md"))
    head = '<section id="sec-inputs">' + h2("Model inputs and features", "inputs")
    return (head + p("This section lists what the soft sensor is allowed to see, what is explicitly kept away from it, how the "
                     "features are derived and which of them the trained models rely on.")
            + raw_inputs() + excluded(data_run) + feature_table(docs) + inversion(metrics) + importance(metrics, pngs)
            + example(model_run, data_run, metrics) + "</section>")
