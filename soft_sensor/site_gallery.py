"""Figure descriptions and the collapsible "All figures" gallery at the top of the page.

DESCRIPTIONS maps an image stem to a function that writes 2-4 sentences (what the graph shows, how to read it, the
key takeaway) from the build inputs: metrics.json, material_curve_<material>.json, summary.json, the simulator
configuration and the validation report. The same text is the long caption of the figure in the body. A stem without
an entry, or whose inputs are missing, gets a generic text built from its caption (recorded in FALLBACKS).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from site_common import ROOT, SPLIT_LABEL, Build, FigureEntry, caption, dig, load_json, num, pct_of, zoom_attrs

TARGET_PCT = 5.0
FALLBACKS: list[str] = []


@dataclass
class Ctx:
    b: Build
    metrics: dict | None = None
    summary: dict | None = None
    cfg: dict | None = None
    model_run: Path = Path(".")
    sim_run: Path = Path(".")
    report: str = ""
    cache: dict[str, Any] = field(default_factory=dict)


CTX: Ctx | None = None


def configure(b: Build, metrics: dict | None, summary: dict | None, cfg: dict | None, model_run: Path,
              sim_run: Path) -> None:
    """Set the inputs the descriptions are written from (call before the sections are rendered)."""
    global CTX
    rep = ROOT / sim_run / "validate_report.txt"
    CTX = Ctx(b, metrics, summary, cfg, model_run, sim_run, rep.read_text() if rep.exists() else "")


# ---------------------------------------------------------------- helpers
class Missing(Exception):
    """An input a description needs is not available."""


def need(value: Any) -> Any:
    if value is None or (isinstance(value, str) and not value):
        raise Missing
    return value


def rx(c: Ctx, pattern: str) -> re.Match:
    """First match of a regex in the validation report (Missing if absent)."""
    return need(re.search(pattern, c.report, flags=re.M))


def lift(c: Ctx, split: str) -> dict:
    return need(dig(c.metrics, "splits", split, "final", "lift"))


def mats_of(block: dict) -> dict[str, dict]:
    return need(block.get("by_material"))


def nice(name: str) -> str:
    return name.replace("_", " ")


def split_of(stem: str) -> str:
    return stem.rpartition("_")[2]


# ---------------------------------------------------------------- model figures
def d_lift_error(c: Ctx) -> str:
    t, u = lift(c, "time"), lift(c, "bins")
    ub = {m: g["mean_pct"] for m, g in mats_of(u).items()}
    best, worst = min(ub, key=ub.get), max(ub, key=ub.get)
    below = [nice(m) for m, v in ub.items() if v < TARGET_PCT]
    return (f"Bars give the mean absolute % error at lift (estimate in the pre-lift hour versus the true mass) per material "
            f"and overall; solid bars are the same-machines split, hatched lighter bars the unseen-machines split, the dashed "
            f"line is the {TARGET_PCT:.0f} % target. Overall the error is {num(t['overall']['mean_pct'])} % on known machines "
            f"and {num(u['overall']['mean_pct'])} % on unseen ones. On unseen machines {nice(best)} is best "
            f"({num(ub[best])} %) and {nice(worst)} worst ({num(ub[worst])} %); "
            + (f"only {', '.join(below)} {'stays' if len(below) == 1 else 'stay'} below the target there." if below
               else "no material is below the target there."))


def _importance_groups(sp: dict) -> list[tuple[list[str], dict]]:
    groups: list[tuple[list[str], dict]] = []
    for mat, gains in need(sp.get("feature_importance_gain")).items():
        hit = next((g for g in groups if g[1] == gains), None)
        if hit:
            hit[0].append(mat)
        else:
            groups.append(([mat], gains))
    return groups


def d_importance(c: Ctx, split: str) -> str:
    sp = need(dig(c.metrics, "splits", split))
    parts, fill_share = [], []
    for mats, gains in _importance_groups(sp):
        total = sum(gains.values()) or 1.0
        fill_share.append(100 * gains.get("fill_ffill", 0.0) / total)
        top = sorted(gains.items(), key=lambda kv: -kv[1])[:2]
        parts.append(f"{' + '.join(nice(m) for m in mats)}: {top[0][0]} {num(100 * top[0][1] / total, 0)} %, "
                     f"{top[1][0]} {num(100 * top[1][1] / total, 0)} %")
    return (f"One panel per model used on the {SPLIT_LABEL[split]} split; each bar is a feature's share of the total "
            "LightGBM split gain (top 15), coloured by material for a per-material model and grey for the shared model. "
            f"The two strongest features per model: {'; '.join(parts)}. The fill reading itself (fill_ffill) gets at most "
            f"{num(max(fill_share))} % of the gain: pressure-derived features carry the density information.")


def _shap_top(c: Ctx, split: str) -> list[tuple[str, str, float, int, float]]:
    key = f"shap_{split}"
    if key not in c.cache:
        mats = list(mats_of(lift(c, split)))
        out = []
        for m in mats:
            path = ROOT / c.model_run / split / f"shap_{m}.parquet"
            if not path.exists():
                continue
            df = pd.read_parquet(path, columns=None)
            cols = [x for x in df.columns if x.startswith("shap__")]
            mean_abs = df[cols].abs().mean().sort_values(ascending=False)
            feat = mean_abs.index[0][len("shap__"):]
            val = df.get(f"value__{feat}")
            r = (float(pd.Series(val, dtype=float).corr(df[f"shap__{feat}"]))
                 if val is not None and pd.api.types.is_numeric_dtype(val) else float("nan"))
            out.append((m, feat, float(mean_abs.iloc[0]), len(df), r))
        c.cache[key] = need(out)
    return c.cache[key]


def d_shap(c: Ctx, split: str) -> str:
    tops = _shap_top(c, split)
    feats = {f for _, f, _, _, _ in tops}
    lead = "; ".join(f"{nice(m)} {f} ({num(v, 0)} kg/m³, {n} lifts)" for m, f, v, n, _ in tops)
    rs = [r for *_, r in tops if r == r]
    return (f"Beeswarm per material on the {SPLIT_LABEL[split]} split: every dot is one test lift, its position the feature's "
            "SHAP contribution to the predicted density in kg/m³ (right = heavier), its colour the feature value "
            "(light = low, dark = high, grey = categorical); features are sorted by mean |SHAP|, top 12. Largest mean "
            f"|SHAP| per material: {lead}. "
            + ("The same feature leads for every material. " if len(feats) == 1 else "")
            + (f"For that leading feature, higher values push the estimate up (correlation of value and SHAP "
               f"{min(rs):+.2f} to {max(rs):+.2f})." if rs and min(rs) > 0 else ""))


def d_pred_vs_true(c: Ctx, split: str) -> str:
    g = lift(c, split)["overall"]
    return (f"Each point is one test lift on the {SPLIT_LABEL[split]} split: x = true (weighbridge) mass, y = estimate in the "
            "hour before the lift, colour/marker = material. Points on the diagonal are exact; the grey band is ±5 %. "
            f"{num(g['n'], 0)} lifts, {pct_of(g['within_5pct'], 0)} % of them inside ±5 %, mean error {num(g['mean_pct'])} %, "
            f"MAE {num(g['mae_kg'], 0)} kg and bias {num(g['bias_kg'], 0)} kg"
            + (" (a slight under-estimate)." if g["bias_kg"] < 0 else "."))


def d_ladder(c: Ctx) -> str:
    def path(split: str) -> str:
        lad = need(dig(c.metrics, "splits", split, "ladder"))
        keys = [k for k in ("naive_fill_x_density", "fill_presses", "pressure", "pressure_graph", "full_selected") if k in lad]
        return " → ".join(num(lad[k]["overall"]["mean_pct"]) for k in keys)
    return ("Mean % error at lift (log scale) as signal families are added from left to right — naive fill × density, + press "
            "counts, + peak pressure, + pressure graph, + weather, per-material, selected; thin lines per material, the "
            "bold line overall with its value, dashed line = 5 % target; left panel same machines, right panel unseen machines. "
            f"Overall: {path('time')} % (same machines) and {path('bins')} % (unseen). The big step is from the naive formula "
            "to fill + presses; peak pressure adds the next clear gain, the later steps a few tenths of a point.")


def d_coverage(c: Ctx) -> str:
    target = pct_of(dig(c.metrics, "settings", "target_coverage") or 0.8, 0)
    low = []
    for split in ("time", "bins"):
        for m, g in mats_of(lift(c, split)).items():
            low.append((g.get("coverage", 1.0), m, split))
    cov, m, split = min(low)
    t, u = lift(c, "time")["overall"], lift(c, "bins")["overall"]
    return (f"Share of true masses inside the calibrated p10–p90 interval, at lift (left) and over every test hour (right); "
            f"solid bars = same machines, hatched = unseen machines, dashed line = the {target} % target. Overall lift "
            f"coverage is {pct_of(t['coverage'], 0)} % (same machines, mean width {num(t['width_kg'], 0)} kg) and "
            f"{pct_of(u['coverage'], 0)} % (unseen, {num(u['width_kg'], 0)} kg). The clear exception is {nice(m)} on "
            f"{SPLIT_LABEL[split]} at {pct_of(cov, 0)} %: too few calibration machines for that material.")


def d_learning(c: Ctx) -> str:
    curve = need(dig(c.metrics, "splits", "bins", "learning_curve"))
    pts = [(x["n_machines"], x["overall"]["mean_pct"]) for x in curve]
    full = lift(c, "bins")["overall"]["mean_pct"]
    n_all = dig(c.metrics, "splits", "bins", "n_train_machines")
    return ("Mean % error at lift on the unseen test machines (y) for the shared model trained on a growing random subset "
            "of training machines (x); thin lines per material, bold line overall, dashed line = 5 % target. "
            f"Overall the error falls from {num(pts[0][1])} % with {pts[0][0]} machines to {num(pts[-1][1])} % with "
            f"{pts[-1][0]} (selected model on all {n_all} machines: {num(full)} %). Most of the gain comes from the first "
            "few dozen machines; beyond that each doubling buys well under a point.")


def d_mass_curves(c: Ctx) -> str:
    h = need(dig(c.metrics, "splits", "time", "final", "hourly", "overall"))
    try:
        from weight_train.plots import CURVE_DAYS, CURVE_MACHINES
        span = f"{CURVE_DAYS} days of the test period for {CURVE_MACHINES} example machines"
    except ImportError:
        span = "a few weeks of the test period for example machines"
    return (f"{span[0].upper()}{span[1:]} (same-machines split), one panel each: black line = true mass, coloured line = "
            "hourly estimate, shaded band = p10–p90 interval, triangles = the pre-lift hour compared with the weighbridge. "
            "The sawtooth rises with loading and drops at each lift; the estimate tracks it hour by hour. "
            f"Over all test hours of this split the mean error is {num(h['mean_pct'])} %, with {pct_of(h['coverage'], 0)} % "
            f"coverage at a mean interval width of {num(h['width_kg'], 0)} kg.")


def d_material_curve(c: Ctx, material: str) -> str:
    res = need(load_json(c.b, c.model_run / f"material_curve_{material}.json"))
    pts = res["points"]
    unseen = [(p["n_machines"], p["unseen"]["mean_pct"]) for p in pts]
    best_n, best = min(unseen, key=lambda t: t[1])
    first_ok = next((n for n, v in unseen if v < TARGET_PCT), None)
    same = [p["same"]["mean_pct"] for p in pts]
    return (f"A {nice(material)}-only model trained on 1 to {pts[-1]['n_machines']} machines (x, log scale): blue = error at "
            f"lift on {res['test_machines']} always-held-out machines, orange = the training machines in later months; bands "
            f"= min–max over repeated machine draws, dashed = 5 % target. Unseen error drops from {num(unseen[0][1])} % "
            f"(1 machine) to a best of {num(best)} % at {best_n} machines, "
            + (f"first below 5 % at {first_ok}. " if first_ok else "never quite reaching 5 %. ")
            + f"On its own machines the model is at {num(min(same))}–{num(max(same))} % from the very first machine.")


# ---------------------------------------------------------------- simulator figures
def d_pressure_graph(c: Ctx) -> str:
    m = rx(c, r"corr\(contact position, true fill\) ([-+]?[\d.]+); corr\(pressure-derived fill_pct, true fill\) ([-+]?[\d.]+)")
    return ("Up to 120 ram strokes of one sampled machine across the year: x = ram position along the stroke, y = pressure, "
            "colour = true fill at that hour (dark = empty, yellow = full). Pressure stays near idle until the ram meets the "
            "pack, then rises; the fuller the container, the earlier the contact point and the higher the end pressure. "
            f"Over all hours at ≥ 35 % fill the contact position correlates {m.group(1)} with true fill — a weaker fill "
            f"signal than the pressure-derived fill_pct ({m.group(2)}), but it adds shape information about the pack.")


def d_pressure_vs_fill(c: Ctx) -> str:
    hi = rx(c, r"peak pressure at true fill >= 85 %: p10 (\d+), p50 (\d+), p90 (\d+) bar")
    idle = rx(c, r"at true fill < 30 % the pressure is ~idle: p50 (\d+) bar")
    corr = re.findall(r"^\s+(\w+)\s+r = ([-+][\d.]+)", c.report, flags=re.M)
    rs = [float(r) for _, r in need(corr)]
    return ("A random sample of hours: x = true fill, y = hourly peak ram pressure, colour = true pack density (viridis, "
            "brighter = denser); left cardboard + paper, right plastic film + mixed waste. Below 30 % fill the pressure "
            f"is near idle (median {idle.group(1)} bar); it climbs with fill to {hi.group(1)}–{hi.group(3)} bar (p10–p90) "
            f"above 85 %. At the same fill denser packs press harder: correlation with density at 85–100 % fill is "
            f"{min(rs):+.2f} to {max(rs):+.2f} across materials — the density information the fill reading lacks.")


def d_pressure_examples(c: Ctx) -> str:
    from site_sections import FILL_BANDS
    thr = dig(c.cfg, "hydraulics", "curve", "contact_threshold_bar") or 15
    hi = rx(c, r"peak pressure at true fill >= 85 %: p10 (\d+), p50 (\d+), p90 (\d+) bar")
    bands = ", ".join(f"{label}" for _, _, label, _ in FILL_BANDS)
    return (f"Up to four randomly chosen strokes from each true-fill band ({bands}; teal, amber, red), pressure against ram "
            f"position; the dot marks the detected contact point, the first sample more than {thr} bar above the stroke "
            "start. Low-fill strokes stay flat almost to the end; at high fill the contact comes earlier and the curve ends "
            f"far higher (fleet median peak at ≥ 85 % fill: {hi.group(2)} bar).")


def d_fill_estimate(c: Ctx) -> str:
    f = c.b.facts
    err, err_hi, n = need(f.get("fill_err_pp")), need(f.get("fill_err_hi_pp")), need(f.get("fill_readings"))
    corr = rx(c, r"corr\(pressure-derived fill_pct, true fill\) ([-+]?[\d.]+)")
    level = dig(c.cfg, "sensors", "false_full", "level_pct")
    if isinstance(level, (list, tuple)):
        level = "–".join(num(x, 0) for x in level)
    return (f"Density of all {num(n, 0)} hourly readings (log colour scale): x = true fill, y = the firmware's "
            "pressure-derived fill_pct; the dashed white line is a perfect reading. The cloud follows the diagonal "
            f"(correlation {corr.group(1)}) with a mean error of {num(err)} pp, {num(err_hi)} pp at true fill ≥ 30 %"
            + (f"; the horizontal band at {level} % reported fill is the spurious 'full' episodes" if level is not None else "")
            + f" — behind {pct_of(dig(c.summary, 'false_full_lift_share'))} % of all lifts.")


def d_lifts(c: Ctx) -> str:
    lm = need(dig(c.summary, "lift_mass_by_material"))
    days = rx(c, r"days between lifts: median ([\d.]+), p5 ([\d.]+), p95 ([\d.]+)")
    means = ", ".join(f"{nice(m)} {num(x['mean'] / 1000, 2)} t" for m, x in lm.items())
    ok = [m for m in lm if re.search(rf"^{m}\s+\d.*\bOK\b", c.report, flags=re.M)]
    verdict = (" — all inside the target ranges checked by the validation report" if len(ok) == len(lm)
               else f" — inside the validation targets: {', '.join(nice(m) for m in ok) or 'none'}")
    return (f"Left: histograms of the true lifted mass per material over all {num(c.summary.get('n_lifts'), 0)} lifts "
            "(tonnes); right: days since the previous lift (clipped at 30). "
            f"Mean lift mass: {means}{verdict}. Lifts are typically "
            f"{days.group(1)} days apart (5–95 %: {days.group(2)}–{days.group(3)} days).")


def d_temperature(c: Ctx) -> str:
    block = need(re.search(r"monthly mean temperature \(C\):\n(.*?)\n\s*\n", c.report, flags=re.S))
    rows = [ln.split() for ln in block.group(1).splitlines()[2:] if ln.strip()]
    temps = {r[0]: [float(x) for x in r[1:]] for r in rows}
    cold = min(temps, key=lambda r: min(temps[r]))
    warm = max(temps, key=lambda r: max(temps[r]))
    frost = sum(1 for v in temps[cold] if v < 0)
    return (f"Monthly mean air temperature of the synthetic weather for each of the {len(temps)} regions (x = calendar "
            f"month; {cold} has {frost} months below 0 °C). The coldest month mean is {num(min(temps[cold]))} °C in {cold}, the warmest {num(max(temps[warm]))} °C in "
            f"{warm}. Months below freezing drive the frost and cold-oil effects in the simulator, which the model has to "
            "see through.")


def d_weather_effect(c: Ctx) -> str:
    vals = re.findall(r"^(\w+_\w+)\s+lift mass\s+([-+][\d.]+) %", c.report, flags=re.M)
    wet = rx(c, r"wet week: rain7 >= (\d+) mm & RH7 >= (\d+) %")
    txt = ", ".join(f"{nice(k)} {v} %" for k, v in need(vals))
    return ("Change in machine-normalised cardboard lift mass after a wet and humid week (≥ "
            f"{wet.group(1)} mm rain, RH ≥ {wet.group(2)} %) relative to a dry week, one bar per infeed type: {txt}. "
            "Rain falling into open hoppers adds water mass and lids keep part of it out; indoor shafts get no rain, so "
            "their difference is noise. The weather effect is real but only a few percent of the lift mass.")


DESCRIPTIONS: dict[str, Callable[[Ctx], str]] = {
    "lift_error_by_material": d_lift_error,
    "feature_importance_time": lambda c: d_importance(c, "time"),
    "feature_importance_bins": lambda c: d_importance(c, "bins"),
    "shap_time": lambda c: d_shap(c, "time"),
    "shap_bins": lambda c: d_shap(c, "bins"),
    "pred_vs_true_time": lambda c: d_pred_vs_true(c, "time"),
    "pred_vs_true_bins": lambda c: d_pred_vs_true(c, "bins"),
    "ladder": d_ladder,
    "coverage_by_material": d_coverage,
    "learning_curve": d_learning,
    "mass_curves_time": d_mass_curves,
    "material_curve_cardboard": lambda c: d_material_curve(c, "cardboard"),
    "pressure_graph": d_pressure_graph,
    "pressure_vs_fill": d_pressure_vs_fill,
    "pressure_examples": d_pressure_examples,
    "fill_estimate": d_fill_estimate,
    "lifts": d_lifts,
    "temperature": d_temperature,
    "weather_effect": d_weather_effect,
}


def describe(stem: str) -> str:
    """The long description of a figure; a generic text from its caption if no data-driven one can be written."""
    fn = DESCRIPTIONS.get(stem)
    if fn is not None and CTX is not None:
        try:
            return fn(CTX)
        except (Missing, KeyError, IndexError, TypeError, ValueError, OSError) as exc:
            logging.warning("figure %s: data-driven description unavailable (%s: %s)", stem, type(exc).__name__, exc)
    FALLBACKS.append(stem)
    return (f"{caption(stem)} Click the image to open it at high resolution; the surrounding section explains the "
            "numbers behind it.")


# ---------------------------------------------------------------- gallery
GALLERY_JS = """<script>(function(){var g=document.getElementById("gallery");if(!g)return;
function load(){if(!g.open)return;g.querySelectorAll("img[data-src]").forEach(function(i){i.src=i.getAttribute("data-src");
i.removeAttribute("data-src");});}g.addEventListener("toggle",load);load();})();</script>"""


def gallery(entries: list[FigureEntry]) -> str:
    """A closed <details> listing every figure of the page; its thumbnails get their src only when it is opened."""
    if not entries:
        return ""
    items = []
    for e in entries:
        alt = escape(e.caption)
        items.append(f'<li class="gitem"><a {zoom_attrs(e)}><img data-src="{escape(e.src)}" alt="{alt}" loading="lazy" '
                     f'decoding="async"><noscript><img src="{escape(e.src)}" alt="{alt}" loading="lazy"></noscript></a>'
                     f'<div class="gtext"><p class="gtitle"><a href="#fig-{escape(e.stem)}"><b>{e.label}.</b></a> '
                     f"{escape(e.caption)}</p><p>{escape(e.description)}</p></div></li>")
    return (f'<details id="gallery" class="gallery"><summary>All figures ({len(entries)}) — click to expand</summary>'
            '<p class="gnote">Every graph of this document in reading order, with what it shows and how to read it. Click '
            "an image to open it at high resolution; click a figure number to jump to it in the text.</p>"
            f'<ol class="glist">{"".join(items)}</ol></details>' + GALLERY_JS)
