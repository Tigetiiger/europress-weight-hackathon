"""One dictionary of domain terms, variables and metrics: drives the hover tooltips and the Glossary section.

build_glossary(cfg, feature_docs) -> {term: definition}; wrap_terms(html) adds tooltip spans to visible text.
"""
from __future__ import annotations

import re
from html import escape

from site_common import dig, h2, num

# hourly data columns a real machine / service / weather feed provides (also used by the model-inputs section)
TEL, SMART, WX, CONF = "machine telemetry", "fleet service", "public weather", "machine configuration"
COLUMNS: dict[str, tuple[str, str]] = {
    "bin_id": ("machine identifier", SMART), "ts": ("local hour start (Europe/Helsinki)", SMART),
    "material": ("material the container is assigned to: cardboard, paper, plastic_film or mixed_waste", CONF),
    "equipment_class": ("COMBI or DUO", CONF), "container_volume_m3": ("container volume in m³", CONF),
    "region": ("region, i.e. the weather station used", CONF),
    "infeed": ("how material enters: open_hopper, lidded_hopper or indoor_shaft", CONF),
    "drainage": ("1 if the container has a drainage system, else 0", CONF),
    "temp_c": ("air temperature of the hour in °C", WX),
    "precip_mm_day": ("day total precipitation in mm; the model uses it only for completed days", WX),
    "rh_pct": ("day mean relative humidity in %; the model uses it only for completed days", WX),
    "fill_pct": ("the machine's fill estimate in %, derived by its firmware from back pressure (not a level sensor)", TEL),
    "presses": ("number of ram strokes in the hour", TEL),
    "peak_pressure_bar": ("highest stroke peak pressure of the hour in bar (NaN without a stroke or during a dropout)", TEL),
    "lift_event": ("1 when the container was emptied at the start of this hour; starts a new cycle", SMART),
    "oil_temp_c": ("hydraulic oil temperature in °C (simulated data column; not a model input — the pressure correction uses the air temperature)", TEL), "energy_kwh": ("motor energy of the hour in kWh", TEL),
    "precip_mm": ("precipitation of the hour in mm (past hours only)", WX),
    "curve_contact_pos": ("pressure graph: ram position (0–1 of the stroke) where the pressure starts rising, hourly mean", TEL),
    "curve_rise_slope_bar": ("pressure graph: slope from the contact point to the peak, hourly mean", TEL),
    "curve_mean_bar": ("pressure graph: mean pressure over the stroke in bar, hourly mean", TEL),
    "curve_completion": ("pressure graph: share of the ram travel completed before the relief valve opened, hourly mean", TEL),
}
TRUTH: dict[str, str] = {
    "true_mass_kg": "simulator truth: mass in the machine at the end of the hour (container plus press chamber); the training label",
    "true_fill_pct": "simulator truth: volumetric fill of the container in %",
    "true_density_kg_m3": "simulator truth: mean pack density in kg/m³",
    "true_moisture_extra": "simulator truth: moisture above the 7 % reference, as a fraction of mass",
    "true_false_full": "simulator truth: a spurious-high (false-full) episode is distorting the fill reading",
    "true_water_humidity_kg": "simulator truth: water taken up from humid air (can be negative: drier than the 7 % reference)",
    "true_water_carried_kg": "simulator truth: water carried in on material stored outdoors",
    "true_water_hopper_kg": "simulator truth: rain and snow that entered through the hopper",
    "true_free_water_kg": "simulator truth: unbound liquid water in the container",
    "true_ice_kg": "simulator truth: frozen water in the container",
    "snow_mm_day": "day total snowfall in mm water equivalent (not a model input)",
    "lifted_mass_kg": "simulator truth: mass removed by a lift in this hour",
    "side_waste_kg": "simulator truth: material that did not fit and was left outside the container",
    "ref_scale_kg": "load-cell reading on about 5 % of machines (true mass ± 0.5 %); a scale, so never a model input",
    "mass_pred_kg": "model output: estimated mass in the container at the end of the hour",
    "mass_p10_kg": "model output: lower end of the calibrated 80 % interval",
    "mass_p90_kg": "model output: upper end of the calibrated 80 % interval",
    "weighbridge_net_kg": "net mass recorded by the transporter's weighbridge for a lift (20 kg steps, ±15 kg noise in the simulator)",
}
LADDER_STEPS: dict[str, str] = {
    "naive_fill_x_density": "ladder step 0: fill reading × nominal material density × container volume; a formula, not a trained model",
    "fill_presses": "ladder step 1: LightGBM on machine, calendar, cycle, fill and press-count features",
    "pressure (ladder step)": "ladder step 2: step 1 plus peak-pressure and per-stroke features",
    "pressure_graph": "ladder step 3: step 2 plus the pressure-graph features",
    "full_shared": "ladder step 4: all features including weather, one model shared across materials",
    "full_per_material": "ladder step 5: all features, one model per material",
    "full_selected": "ladder step 6: shared or per-material model, chosen per material on the validation slice; the deployed configuration",
}
METRICS: dict[str, str] = {
    "mean % error": "mean of |estimate − true mass| / true mass × 100 over the evaluated hours; lifts below 200 kg are excluded from percentage metrics",
    "median % error": "median of the per-lift percentage errors (half the lifts are better than this)",
    "within 5 %": "share of lifts whose percentage error is at most 5 %; the hackathon target is a mean error below 5 %",
    "within 10 %": "share of lifts whose percentage error is at most 10 %",
    "MAE": "mean absolute error in kg: mean of |estimate − true mass|",
    "bias": "mean signed error (estimate − true mass) in kg; negative means the sensor under-estimates on average",
    "coverage": "share of true values that fall inside the p10–p90 interval; the calibration target is 80 %",
    "width": "mean width of the p10–p90 interval in kg; narrower is more informative at the same coverage",
    "p10": "lower 10 % quantile estimate: the true mass should fall below it in 10 % of cases",
    "p90": "upper 90 % quantile estimate: the true mass should fall above it in 10 % of cases",
    "p10–p90 interval": "80 % prediction interval from two quantile models, widened by conformal calibration",
    "WAPE": "weighted absolute percentage error: sum of |errors| divided by sum of true masses (used only in the early baseline)",
    "p90 error": "the 90th percentile of the per-lift percentage errors (used only in the early baseline)",
    "pp": "percentage points: the arithmetic difference between two percentages",
    "cv": "coefficient of variation: standard deviation divided by the mean",
    "correlation": "Pearson correlation coefficient, −1 to 1",
}
SPLITS: dict[str, str] = {
    "unseen machines": "the bins split: 25 % of machines (stratified by material) are never trained on and are tested on the last 25 % of the period; the new-installation case",
    "same machines, later months": "the time split: all machines are trained on the first 75 % of the period and tested on the last 25 %; the pilot-fleet case",
    "validation slice": "held-out rows used to tune hyper-parameters, stop training early and choose shared versus per-material; never the test rows",
    "calibration slice": "held-out rows used only to calibrate the interval width (conformal calibration); never the test rows",
    "test split": "rows used once, for the reported numbers",
    "pre-lift hour": "the last hour before a lift; its mass estimate is the value compared with the weighbridge",
    "comparison ladder": "the same test lifts evaluated with feature sets that add one signal family at a time, to show what each signal contributes",
    "learning curve": "error on the same unseen test machines when the model is trained on 10, 25, 50, 100 or all training machines",
}
ML: dict[str, str] = {
    "LightGBM": "open-source gradient-boosted decision tree library (MIT licence); all point and quantile models here are LightGBM regressors",
    "gradient boosting": "ensemble method that adds small decision trees one after another, each fitting the remaining error",
    "quantile regression": "fitting a model to a quantile (here p10 or p90) of the target instead of its mean, using the pinball loss",
    "quantile": "the value below which a given share of observations falls",
    "conformal calibration": "normalised conformalised quantile regression: on a separate calibration slice the score max(p10 − y, y − p90) / width is computed and the interval is widened by its finite-sample 80 % quantile, so the stated coverage holds on exchangeable data",
    "CQR": "conformalised quantile regression, see conformal calibration",
    "Mondrian": "conformal calibration done separately per stratum, here material × fill bucket (<50, 50–80, ≥80 %), with fallback to the material and then to the pool when a cell has fewer than 30 points",
    "shared model": "one LightGBM model for all materials, with material as a categorical feature",
    "per-material model": "a separate LightGBM model per material, using the shared model's tuned parameters",
    "selected setup": "for each material, shared or per-material model, whichever had the lower mean % error on the validation slice",
    "SHAP": "SHapley Additive exPlanations: per-row attribution of a prediction to each feature (exact TreeSHAP from LightGBM)",
    "gain": "LightGBM feature importance: total reduction of the training loss obtained by splits on that feature",
    "feature importance": "share of total split gain per feature in the model that a material actually uses",
    "monotone constraint": "a tree constraint forcing the prediction to never decrease when a feature increases; kept on fill_ffill and presses_cycle when it costs less than 2 % validation error",
    "early stopping": "training stops when the validation error has not improved for 50 rounds",
    "num_leaves": "LightGBM parameter: maximum number of leaves per tree (31 or 127 in the grid)",
    "min_data_in_leaf": "LightGBM parameter: minimum rows per leaf (50 or 200 in the grid)",
    "leakage": "information about the answer reaching the inputs, for example simulator truth columns or future rows; it would make the test numbers meaningless",
    "whitelist": "the fixed list of input columns the pipeline may read; every other column is dropped before any feature is computed",
    "kg/m³ target": "the model predicts mass per m³ of container (true mass / container volume) and multiplies back by the volume, so one model covers all container sizes",
    "nominal density": "public compacted density per material used by the naive estimate: cardboard 190, paper 330, plastic film 140, mixed waste 400 kg/m³",
    "pressure inversion": "fitting P / visc(oil) − 25 bar = k · fill^a · ρ^b per material on training rows and inverting it to a pressure-implied density; used only at fill > 25 % and P < 228 bar",
    "forward-filled": "a missing value is replaced by the last valid value within the same cycle",
    "EWMA": "exponentially weighted moving average; the half-life says after how many observations a value's weight has halved",
    "feature family": "a group of features computed from the same signal: machine, calendar, cycle, fill, presses, peak pressure, strokes, pressure graph, weather",
    "5-fold cross-validation": "the machines are divided into 5 groups; each group is tested in turn with a model trained on the other four",
    "deterministic": "same data and seed give bit-identical output",
}
DOMAIN: dict[str, str] = {
    "soft sensor": "a software estimate of a quantity that is not measured directly; here the mass of waste, from signals the machine already has",
    "back pressure": "hydraulic pressure the ram meets from the waste pack, above the idle pressure of an unloaded stroke",
    "fill": "share of the container volume occupied by waste; the machine reports an estimate of it (fill_pct)",
    "fill reading": "the machine's own fill estimate (fill_pct), derived from a rolling average of back pressure; about 5–6 percentage points mean error",
    "lift": "emptying of the container: a COMBI unit is lifted and emptied, a DUO container is swapped; the lifted mass is weighed on the transporter's weighbridge",
    "weighbridge": "the truck scale that gives the net mass of a lift; the reference the soft sensor is compared against",
    "false-full": "a spurious-high fill reading caused by a jammed or bridging item: the machine reads 88–97 % full while the true fill is much lower, often triggering an early lift",
    "springback": "right after a stroke the pack is compressed a few % denser and relaxes back over about 3 hours, so the fill reading creeps up after pressing",
    "relief valve": "pressure limit of the hydraulic system; when it opens the pressure saturates and carries no more information",
    "oil viscosity": "cold hydraulic oil is more viscous and raises the measured pressure; the simulator uses +0.4 % per °C below 40 °C",
    "pressure graph": "the pressure recorded along one ram stroke (16 samples); its shape gives the contact point, rise slope, mean pressure and completion",
    "contact point": "ram position in the stroke where the pressure starts rising, i.e. where the ram meets the pack face; a geometric fill indicator",
    "peak pressure": "the highest pressure reached in a stroke",
    "ram": "the hydraulic plate that pushes the waste into the container",
    "stroke": "one forward movement of the ram; also called a press or press cycle",
    "press chamber": "the space in front of the ram where loads wait until the next stroke",
    "hopper": "the opening through which waste is thrown in; open, lidded or fed from an indoor shaft",
    "infeed type": "how material enters: open hopper (outdoors, rain can enter), lidded hopper (mostly covered) or indoor shaft (no rain)",
    "COMBI": "compactor type in which press and container form one unit that is lifted and emptied together (10–22 m³)",
    "DUO": "compactor type with a stationary press and a separate container that is swapped at a lift (20–35 m³); keeps a 0–2 % residue",
    "drainage system": "an outlet that lets free water leave the container instead of pooling",
    "side waste": "material that did not fit into a full container and was left outside; also called overflow",
    "residue": "the 0–2 % of the layers a DUO container keeps when swapped",
    "cycle": "the period between two lifts; features marked 'since lift' or 'cycle' are computed within it",
    "tare": "empty weight of the container (and press, for COMBI) that is subtracted from the gross vehicle load",
    "load cell": "a weighing sensor; about 5 % of the simulated machines carry one as reference, never used by the model",
    "dropout": "an hour without a valid telemetry reading (1 % of hours plus longer outages)",
    "order threshold": "fill reading at which a lift is ordered: 80, 85 or 90 % depending on the site",
    "burn-in": "28 simulated days before the reported period that give every machine a random starting state",
    "mass balance": "check that start + material in + hopper precipitation − drained − seeped − side waste − lifted = end for every machine",
    "pack density": "mass per volume of the compacted waste; it rises with pack pressure, moisture and the machine's press force",
    "density factor": "hidden per-machine multiplier of the typical density (lognormal, 15 % cv); what the pressure has to reveal",
    "compressibility": "material constant c of the log-law density growth with pack pressure",
    "moisture extra": "moisture content above the 7 % reference, as a fraction of mass; wet fibre is heavier and softer",
    "hygroscopic": "takes up water from humid air (fibre); plastics do not",
    "free water": "liquid water in the container that is not bound in the material; adds mass but no volume",
    "frost": "below 0 °C free water and hopper snow become ice and wet loads freeze into lumps that take more volume",
    "frozen lumps": "wet loads entering below 0 °C take 8–15 % more volume until they thaw over about 12 hours",
    "yard wetness": "index 0–1 of how wet the outdoor storage area is (recent rain and melt), driving carry-in water",
    "carry-in": "water brought in on the surface of material stored outdoors",
    "floating roof": "a cover that presses on the pack in large cardboard containers (+10 % density)",
    "sector": "type of site: retail, warehouse, industry or hotel; sets opening hours and seasonal pattern",
    "region": "one of five Finnish weather regions: Uusimaa, Varsinais-Suomi, Pirkanmaa, Pohjois-Pohjanmaa, Lappi",
    "FMI": "Finnish Meteorological Institute; the simulator can replay its daily observations instead of synthetic weather",
    "Oswin isotherm": "equilibrium moisture content of fibre as a function of relative humidity: MC = A · (h/(1−h))^B",
    "EMC": "equilibrium moisture content: the moisture fibre settles to at a given relative humidity",
    "AR(1)": "first-order autoregressive process: today's anomaly is φ times yesterday's plus noise",
    "lognormal": "a distribution whose logarithm is normal; used for cycle lengths and daily scatter",
    "Poisson": "count distribution for the number of loading events per hour",
    "gamma distribution": "skewed positive distribution used for event masses and daily rain amounts",
    "Dirichlet": "distribution over shares that sum to one; spreads a day's rain over its rain hours",
    "Markov chain": "wet/dry day sequence where the probability of a wet day depends on the previous day",
    "Gaussian copula": "a way to give the regions correlated rain while keeping each region's own distribution",
    "stiffness": "per-material factor of how fast back pressure rises with fill (cardboard 1.00, paper 1.10, film 0.85, mixed waste 0.90)",
    "idle pressure": "pressure of an unloaded stroke (20–30 bar per machine); subtracted to get the back pressure",
    "pack pressure": "pressure the pack actually receives; the layer's highest pack pressure (pmax) sets its density",
    "hexbin plot": "two-dimensional histogram with hexagonal cells; colour shows how many points fall in each cell",
}
# aliases -> canonical key (plurals, spellings)
ALIASES: dict[str, str] = {
    "lifts": "lift", "strokes": "stroke", "weighbridges": "weighbridge", "false full": "false-full",
    "spurious-high": "false-full", "false-full episode": "false-full", "false-full episodes": "false-full",
    "cycles": "cycle", "hoppers": "hopper", "infeed types": "infeed type", "contact position": "contact point",
    "viscosity": "oil viscosity", "soft sensors": "soft sensor", "hydraulic soft sensor": "soft sensor",
    "pressure graphs": "pressure graph", "drainage": "drainage system", "overflow": "side waste",
    "80 % interval": "p10–p90 interval", "80 % prediction interval": "p10–p90 interval",
    "mean error": "mean % error", "mean % err": "mean % error", "median %": "median % error",
    "coverage %": "coverage", "bias kg": "bias", "MAE kg": "MAE", "width kg": "width",
    "percentage points": "pp", "load cells": "load cell", "dropouts": "dropout", "outages": "dropout",
    "layers": "pack density", "ewm": "EWMA", "EMA": "EWMA", "forward-fill": "forward-filled", "ffill": "forward-filled",
    "bins split": "unseen machines", "time split": "same machines, later months",
    "per material": "per-material model", "per_material": "per-material model", "shared": "shared model",
    "conformal": "conformal calibration", "conformalised": "conformal calibration", "monotone constraints": "monotone constraint",
    "cross-validation": "5-fold cross-validation", "leak": "leakage", "frozen lump": "frozen lumps",
    "gamma": "gamma distribution", "Gamma": "gamma distribution", "correlation coefficient": "correlation", "corr": "correlation",
    "fill level": "fill", "fill estimate": "fill reading", "density scatter": "density factor",
}
CATEGORY_LABEL = {"col": "hourly column", "truth": "truth / output column", "feat": "feature", "step": "ladder step",
                  "metric": "metric", "split": "evaluation", "ml": "modelling", "dom": "domain", "param": "simulator parameter"}


def _params(cfg: dict | None) -> dict[str, str]:
    def cv(path: str, nd: int | None = None) -> str:
        val = dig(cfg, *path.split("."))
        if val is None:
            return "–"
        return num(val, nd) if nd is not None and isinstance(val, (int, float)) else str(val)
    return {
        "rho_typ": f"typical compacted density per material at the reference pack pressure of {cv('compaction.p_ref_bar', 0)} bar: "
                   f"{cv('compaction.rho_typ_kg_m3')} kg/m³",
        "P_idle": f"idle pressure of an unloaded stroke, drawn per machine from {cv('hydraulics.p_idle_bar')} bar",
        "relief": f"relief-valve pressure: {cv('hydraulics.relief_bar', 0)} bar, cv {cv('hydraulics.relief_cv')} across machines",
        "fill_onset": f"fill fraction from which back pressure becomes visible: {cv('hydraulics.fill_onset')} (typically 5–10 %)",
        "fill_exponent": f"exponent of the fill term in the pressure law: {cv('hydraulics.fill_exponent')} (linear)",
        "density_exponent": f"exponent of the density term in the pressure law: {cv('hydraulics.density_exponent')} (medium dependence)",
        "ram_ingress_frac": f"share of the stroke the ram travels inside the container: {cv('hydraulics.curve.ram_ingress_frac')} (the ram enters only 20–30 cm)",
        "k": f"hydraulic scale of the pressure law: {cv('hydraulics.k_bar', 0)} bar, cv {cv('hydraulics.k_machine_cv')} across machines",
        "P_ref": f"pack pressure at which the density equals rho_typ: {cv('compaction.p_ref_bar', 0)} bar",
        "P0": f"pressure scale of the log-law density growth: {cv('compaction.p0_bar', 0)} bar",
        "pmax": "highest pack pressure a layer has experienced; compaction is plastic, so it only grows",
        "fill_mode": f"how the simulated machine reports fill: {cv('sensors.fill_mode')} (firmware estimate from pressure) or ultrasonic (direct level sensor)",
        "pressure_derived": "fill reading computed by the firmware from a rolling average of back pressure, as on a real machine without a level sensor",
        "wet_softening": f"how much a wet pack yields at the same pressure: factor 1 − coeff · moisture_extra, coeff {cv('hydraulics.wet_softening')}",
        "frost_stiffening": f"pressure increase factor for a frozen pack: 1 + {cv('hydraulics.frost_stiffening')} × frozen share",
        "rolling_strokes": f"number of strokes the firmware averages before updating the fill reading: about {cv('sensors.pressure_fill.rolling_strokes')}",
        "full_backpressure_bar": f"back pressure above idle that the firmware reads as 100 % full: {cv('sensors.pressure_fill.full_backpressure_bar', 0)} bar × material stiffness, calibrated per installation",
        "weighbridge noise": f"simulated weighbridge: {cv('operations.weighbridge.resolution_kg', 0)} kg steps, {cv('operations.weighbridge.noise_sd_kg', 0)} kg standard deviation",
        "max interval": f"a lift is scheduled at the latest after {cv('operations.max_interval_days', 0)} days, ordered {cv('operations.scheduled_lead_h', 0)} h ahead",
    }


def build_glossary(cfg: dict | None, feature_docs: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Returns (term -> definition, term -> category) with every source merged; later sources do not override."""
    out: dict[str, str] = {}
    cat: dict[str, str] = {}
    for group, label in ((DOMAIN, "dom"), (METRICS, "metric"), (SPLITS, "split"), (ML, "ml"), (LADDER_STEPS, "step"),
                         ({k: v for k, (v, _) in COLUMNS.items()}, "col"), (TRUTH, "truth"), (_params(cfg), "param")):
        for k, v in group.items():
            out.setdefault(k, v)
            cat.setdefault(k, label)
    for k, v in feature_docs.items():
        if k not in out:
            out[k], cat[k] = v, "feat"
    return out, cat


# ---------------------------------------------------------------- automatic wrapping
SKIP_TAGS = {"a", "pre", "script", "style", "h1", "h2", "h3", "h4", "h5", "h6", "nav", "button", "svg", "title", "textarea", "summary"}
# plain words that are also column/feature names: wrapped only when they appear inside <code>
CODE_ONLY = {"hour", "weekday", "material", "presses", "ts", "drainage", "infeed", "k", "relief", "width"}
BLOCK_TAGS = {"p", "li", "td", "th", "figcaption", "dd", "dt", "blockquote", "caption", "div", "section", "h5"}
VOID = {"img", "br", "hr", "meta", "link", "input", "source", "wbr"}


def _pattern(keys: list[str]) -> re.Pattern:
    def alt(k: str) -> str:
        ident = "_" in k or re.fullmatch(r"[A-Z0-9()\- ]{2,}", k) is not None or re.search(r"\d", k) is not None
        return re.escape(k) if ident else f"(?i:{re.escape(k)})"
    alts = sorted(keys, key=len, reverse=True)
    return re.compile(r"(?<![\w.\-/])(?:" + "|".join(alt(k) for k in alts) + r")(?![\w/])")


def wrap_terms(html: str, glossary: dict[str, str], skip_ids: tuple[str, ...] = ("sec-glossary",)) -> str:
    """Wrap the first occurrence of each glossary term inside every block element with a tooltip span."""
    keys = list(glossary) + [a for a, k in ALIASES.items() if k in glossary]
    lower = {k.lower(): k for k in glossary}
    lower.update({a.lower(): k for a, k in ALIASES.items() if k in glossary})
    pat_code = _pattern(keys)                                   # inside <code>: every term
    pat_text = _pattern([k for k in keys if k not in CODE_ONLY])  # in prose: plain words that are also columns are left alone
    tokens = re.split(r"(<[^>]+>)", html)
    stack: list[tuple[str, bool]] = []
    skip_depth, seen, out = 0, set(), []

    def sub(m: re.Match) -> str:
        key = m.group(0) if m.group(0) in glossary else lower.get(m.group(0).lower())
        if key is None or key in seen:
            return m.group(0)
        seen.add(key)
        return f'<span class="term" tabindex="0" data-term="{escape(key)}">{m.group(0)}</span>'

    for tok in tokens:
        if tok.startswith("<"):
            m = re.match(r"<(/?)([a-zA-Z0-9]+)", tok)
            if m and not tok.startswith("<!"):
                closing, tag = m.group(1) == "/", m.group(2).lower()
                if not closing and tag not in VOID and not tok.endswith("/>"):
                    skip = (tag in SKIP_TAGS or 'class="term"' in tok or 'class="n"' in tok
                            or any(f'id="{i}"' in tok for i in skip_ids))
                    stack.append((tag, skip))
                    skip_depth += skip
                    if tag in BLOCK_TAGS:
                        seen = set()
                elif closing:
                    while stack:
                        t, s = stack.pop()
                        skip_depth -= s
                        if t == tag:
                            break
            out.append(tok)
        elif skip_depth or not tok.strip():
            out.append(tok)
        else:
            in_code = bool(stack) and stack[-1][0] == "code"
            out.append((pat_code if in_code else pat_text).sub(sub, tok))
    return "".join(out)


# ---------------------------------------------------------------- section 8
def glossary_section(glossary: dict[str, str], cats: dict[str, str]) -> str:
    items = sorted(glossary.items(), key=lambda kv: kv[0].lower().lstrip("+"))
    rows = []
    for k, v in items:
        label = CATEGORY_LABEL.get(cats.get(k, ""), "")
        name = f"<code>{escape(k)}</code>" if cats.get(k) in ("col", "truth", "feat", "step", "param") and " " not in k else escape(k)
        rows.append(f'<dt id="g-{escape(re.sub(r"[^a-z0-9]+", "-", k.lower()))}">{name}<span class="cat">{escape(label)}</span></dt>'
                    f"<dd>{escape(v)}</dd>")
    return ('<section id="sec-glossary">' + h2("Glossary", "glossary")
            + f"<p>Every term that carries a hover explanation in the text, in alphabetical order ({len(items)} entries). "
              "The same text is shown in the tooltips. Feature definitions are those of the training pipeline; parameter "
              "values are those of the simulator configuration used for the 400-machine run.</p>"
            + '<dl class="gloss">' + "".join(rows) + "</dl></section>")
