"""Section 5: the simulator that generated the data (formulas and parameters read from the run's configuration)."""
from __future__ import annotations

import logging
import re
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

from site_common import (IMG, ROOT, Build, dig, figures, h2, h3, h4, note, num, p, pct_of, pre, raw_table, save_figure, sci,
                         table)

SHOWN_IN_HYDRAULICS = ("pressure_graph", "pressure_vs_fill")
GEN = IMG / "gen"                  # site-relative folder for images made by this page
GEN_DPI = 110                      # inline resolution of those images (the hi-res twin is HIRES_FACTOR x this)
LIFT_TARGET_T = {"cardboard": "2–3.5", "paper": "2.5–4", "plastic_film": "1.2–2.2", "mixed_waste": "3–6 (assumed)"}
FILL_BANDS = ((10, 25, "low (10–25 %)", "#2a9d8f"), (45, 60, "medium (45–60 %)", "#c98a00"),
              (85, 100, "high (85–100 %)", "#d62828"))


def v(cfg: dict | None, path: str, nd: int | None = None) -> str:
    """Config value at a dotted path, html-escaped; '–' when absent."""
    val = dig(cfg, *path.split("."))
    if val is None:
        return "–"
    if nd is not None and isinstance(val, (int, float)):
        return escape(num(val, nd))
    if isinstance(val, (list, tuple)):
        return escape("–".join(str(x) for x in val))
    return escape(str(val))


def why(text: str) -> str:
    return f'<p class="muted"><strong>Relevance for the model:</strong> {escape(text)}</p>'


def shares(d: dict | None, pct: bool = True) -> str:
    if not isinstance(d, dict):
        return "–"
    return escape(", ".join(f"{k} {num(x * 100, 0)} %" if pct else f"{k} {x}" for k, x in d.items()))


# ---------------------------------------------------------------- 5.1 fleet
def fleet(cfg: dict) -> str:
    fc = cfg.get("fleet", {})
    comp = cfg.get("compaction", {})
    rows = []
    for mat, opts in (fc.get("containers") or {}).items():
        sizes = " · ".join(f"{c} {vol} m³ ({num(w * 100, 0)} %)" for c, vol, w in opts)
        rows.append([escape(mat), escape(num(dig(fc, "material_shares", mat) * 100, 0)) + " %",
                     escape(num(dig(comp, "rho_typ_kg_m3", mat), 0)), escape(sizes)])
    models = [[escape(k), escape(str(m.get("force_t"))), escape(str(m.get("cycle_s"))), escape(str(m.get("motor_kw"))),
               escape(" × ".join(str(a) for a in m.get("aperture_m", [])))] for k, m in (fc.get("press_models") or {}).items()]
    sectors = []
    for name, s in (cfg.get("sectors") or {}).items():
        op = s.get("open", {})
        txt = " · ".join(f"{d} {'closed' if h is None else f'{h[0]}–{h[1]}'}" for d, h in op.items())
        sectors.append([escape(name), escape(txt), "closed" if s.get("closed_on_holidays") else "open"])
    sec_mat = [[escape(m)] + [escape(num(x * 100, 0)) + " %" for x in d.values()] for m, d in (fc.get("sector_by_material") or {}).items()]
    sec_head = ["material"] + list(next(iter((fc.get("sector_by_material") or {"": {}}).values())).keys())
    return (h3("Fleet and sites", "sim-fleet")
            + p(f"{v(cfg, 'run.n_bins')} machines, each its own site. Materials are drawn with exact shares (largest remainder). "
                "<strong>mixed_waste</strong> (household waste including bio waste: heavy and less condensed) replaced the brief's "
                "mixed plastic.")
            + raw_table(["material", "share", "rho_typ kg/m³", "containers (class, volume, weight)"], rows, left_cols={0, 3},
                        caption="Materials, typical densities and container mix.")
            + p("<strong>COMBI</strong>: press and container are one unit, lifted together (tare "
                f"{v(cfg, 'fleet.tare_kg.COMBI')} kg, assumed); empties fully. <strong>DUO</strong>: stationary press, the "
                f"container is swapped (tare {v(cfg, 'fleet.tare_kg.DUO')} kg) and keeps a residue of "
                f"{v(cfg, 'operations.duo_residue_frac')} of the layers. Press models within each class: "
                f"{shares(dig(fc, 'press_model_shares', 'COMBI'))} (DUO the same).")
            + raw_table(["press model", "force t", "cycle s", "motor kW", "aperture m"], models, caption="Press models.")
            + p(f"Infeed: {shares(fc.get('infeed_shares'))}. Drainage on {v(cfg, 'fleet.drainage_share')} of machines; "
                f"{v(cfg, 'fleet.waterproof_share_of_undrained')} of the undrained are sealed (water pools); "
                f"{v(cfg, 'fleet.indoor_installation_share_of_shaft')} of indoor shafts sit in a heated waste room at "
                f"{v(cfg, 'moisture.indoor_installation_temp_c')} °C. Floating roof on {v(cfg, 'fleet.floating_roof.share')} "
                f"of cardboard containers ≥ {v(cfg, 'fleet.floating_roof.min_volume_m3')} m³ (+"
                f"{v(cfg, 'fleet.floating_roof.density_gain')} density). Order threshold "
                f"{shares({f'{k} %': x for k, x in (fc.get('threshold_pct_shares') or {}).items()})}. Regions: "
                f"{shares(fc.get('region_shares'))}. {v(cfg, 'fleet.ref_scale_share')} of machines carry a reference load cell.")
            + raw_table(["sector", "opening hours (local)", "public holidays"], sectors, left_cols={0, 1}, caption="Site sectors.")
            + raw_table(sec_head, sec_mat, caption="Sector mix per material.")
            + p(f"Per-site opening hours shift by {v(cfg, 'fleet.opening_shift_h')} h. Each site's daily input is set so the "
                "container reaches its order threshold in a lognormal number of days (median "
                f"{v(cfg, 'fleet.target_cycle_days.median')}, σ_log {v(cfg, 'fleet.target_cycle_days.sigma_log')}, clipped "
                f"{v(cfg, 'fleet.target_cycle_days.min')}–{v(cfg, 'fleet.target_cycle_days.max')} d).")
            + why("material, container size and site rhythm set the mass range and the cycle length; the soft sensor must work "
                  "across all of them, including container sizes it has not seen often."))


# ---------------------------------------------------------------- 5.2 weather
def weather(cfg: dict) -> str:
    w = cfg.get("weather", {})
    rows = [[escape(k), escape(r.get("name", "")), escape(str(r.get("jan_c"))), escape(str(r.get("jul_c"))),
             escape(str(r.get("precip_mm_year"))), escape(str(r.get("wet_day_prob"))), escape(str(r.get("rh_mean")))]
            for k, r in (w.get("regions") or {}).items()]
    t, pr_, h = "weather.temperature", "weather.precipitation", "weather.humidity"
    return (h3("Weather", "sim-weather") + p(f"Mode <code>{v(cfg, 'weather.mode')}</code>: daily weather per region (or replayed "
                                             "FMI daily observations), then disaggregated to hours.")
            + raw_table(["region", "name", "Jan °C", "Jul °C", "precip mm/yr", "p(wet day)", "RH mean %"], rows, {0, 1},
                        caption="Regional climate targets of the weather generator.")
            + pre(f"""
T_day(r,d) = (Jan_r + Jul_r)/2 + (Jul_r - Jan_r)/2 * season(d)  + sd(d) * a(r,d)
             season: cosine, coldest on day-of-year {v(cfg, t + '.coldest_doy')}
a(r,d)     = {v(cfg, t + '.anomaly_ar1_phi')} * a(r,d-1) + sqrt(1 - phi^2) * z     (unit-variance AR(1))
             z: {v(cfg, t + '.spatial_corr')} of the variance shared by all regions
sd(d)      = {v(cfg, t + '.anomaly_sd_summer_c')} °C (summer) ... {v(cfg, t + '.anomaly_sd_winter_c')} °C (winter)

wet(d)     = Markov chain: p11 - p01 = {v(cfg, pr_ + '.persistence')}, p(wet) seasonal ±{v(cfg, pr_ + '.seasonal_amp_prob')} (peak doy {v(cfg, pr_ + '.peak_doy')})
amount     ~ Gamma(shape {v(cfg, pr_ + '.gamma_shape')}), mean scaled to the annual total, seasonal ±{v(cfg, pr_ + '.seasonal_amp_amount')}, min {v(cfg, pr_ + '.min_wet_mm')} mm
             spatial correlation {v(cfg, pr_ + '.spatial_corr')} (Gaussian copula)
RH_day     = rh_mean + rh_amp * season(peak doy {v(cfg, h + '.peak_doy')}) + {v(cfg, h + '.wet_day_bump_pct')} % on wet days + AR(1)(phi {v(cfg, h + '.ar1_phi')}, sd {v(cfg, h + '.noise_sd_pct')} %)

hourly T   = interpolated daily mean + diurnal cosine (amp {v(cfg, t + '.diurnal_amp_winter_c')}–{v(cfg, t + '.diurnal_amp_summer_c')} °C, max at {v(cfg, t + '.warmest_hour')}:00)
             + AR(1) noise (sd {v(cfg, t + '.hourly_noise_sd_c')}, phi {v(cfg, t + '.hourly_noise_phi')}), re-centred to the exact daily mean
hourly rain: block of {v(cfg, pr_ + '.hours_base')} + {v(cfg, pr_ + '.hours_per_sqrt_mm')}*sqrt(mm) + Exp(1) hours, Dirichlet({v(cfg, pr_ + '.dirichlet_alpha')}) weights
snow       : precipitation while T < {v(cfg, pr_ + '.snow_below_c')} °C; snowpack melts {v(cfg, 'weather.snowpack.degree_day_mm_per_c_day')} mm per °C-day
yard wet   : store = store * exp(-1/{v(cfg, 'weather.yard.wet_decay_h')} h) + rain + melt;  index = min(1, store / {v(cfg, 'weather.yard.wet_ref_mm')} mm)
indoor RH  : outdoor vapour pressure at {v(cfg, h + '.indoor.temp_c')} °C (Magnus) + {v(cfg, h + '.indoor.moisture_source_pct')} %, clipped {v(cfg, h + '.indoor.clip_pct')} %""")
            + why("weather is the only public input that changes how heavy a load is at the same volume (wet fibre), and frost "
                  "and cold oil change the pressure signal; it has to look like Finnish weather."))


# ---------------------------------------------------------------- 5.3 inflow
def inflow(cfg: dict) -> str:
    i = "inflow"
    masses = ", ".join(f"{k} {x} kg" for k, x in (dig(cfg, "inflow", "event_mass_mean_kg") or {}).items())
    return (h3("Material inflow", "sim-inflow") + pre(f"""
lambda(h)  = daily_input / mean_event_mass * hour_weight(sector,h) * day_factor * month_factor * open(h)
             * daily_noise(d) * bulk(d)                    (normalised so the year averages to daily_input)
events(h)  ~ Poisson(lambda(h))
mass(h)    ~ Gamma(events * {v(cfg, i + '.event_mass_gamma_shape')}, mean_event_mass / {v(cfg, i + '.event_mass_gamma_shape')})   (sum of gamma event masses)
daily_noise: AR(1) lognormal, phi {v(cfg, i + '.daily_noise.ar1_phi')}, sigma_log {v(cfg, i + '.daily_noise.sigma_log')}
bulk(d)    = x U{v(cfg, i + '.bulk_day.multiplier')} on {v(cfg, i + '.bulk_day.prob_per_open_day')} of open days, else 1
presses(h) ~ Binomial(events, {v(cfg, i + '.press_prob_per_event')}) + Poisson({v(cfg, i + '.test_cycle_rate_per_open_hour')} test cycles per open hour)""")
            + p(f"Mean event mass: {escape(masses)}. Finnish public holidays (including Easter, Ascension, Midsummer, All Saints) "
                "close every sector except hotels.")
            + why("press counts and timing are real machine signals; tying them to loading events makes 'presses since lift' a "
                  "genuine proxy for the number of loads."))


# ---------------------------------------------------------------- 5.4 moisture
def moisture(cfg: dict) -> str:
    m = "moisture"
    return (h3("Moisture pathways", "sim-moisture")
            + p("Each pathway is tracked as its own truth column (<code>true_water_humidity_kg</code>, "
                "<code>true_water_carried_kg</code>, <code>true_water_hopper_kg</code>, <code>true_free_water_kg</code>, "
                "<code>true_ice_kg</code>), so the effect of each can be checked separately.")
            + pre(f"""
humidity  EMC% = {v(cfg, m + '.emc_oswin.A')} * (h/(1-h))^{v(cfg, m + '.emc_oswin.B')}   (Oswin, h = RH/100 <= {v(cfg, m + '.emc_oswin.rh_max')})
          storage air = {v(cfg, m + '.storage_time_constant_h')} h EMA of outdoor / indoor RH, mixed by the site's outdoor-storage share
          extra = hygroscopic * (EMC - {v(cfg, m + '.reference_mc_pct')}) / (100 + {v(cfg, m + '.reference_mc_pct')})     hygroscopic: {shares(dig(cfg, m, 'hygroscopic'), False)}
carry-in  water = mass * carry_in_frac * yard_wetness   (outdoor infeeds only)   carry_in_frac: {shares(dig(cfg, m, 'carry_in_frac'), False)}
          absorbed share {shares(dig(cfg, m, 'carry_absorbed_frac'), False)}; the rest runs off as free water
hopper    rain/snow kg = mm * aperture area * ingress  (open {v(cfg, m + '.hopper_ingress.open_hopper')}, lidded {v(cfg, m + '.hopper_ingress.lidded_hopper')}, shaft {v(cfg, m + '.hopper_ingress.indoor_shaft')}), pressed in with the next stroke
          soaked up by the pack: {shares(dig(cfg, m, 'retained_frac'), False)}
free water: drainage -> drained;  standard container -> seeps out (tau {v(cfg, m + '.seepage_time_constant_h')} h);  sealed -> pools
          free water and ice add mass, not volume
frost     below {v(cfg, m + '.frost.freeze_below_c')} °C (container temp = {v(cfg, m + '.container_temp_time_constant_h')} h EMA of air): free water and hopper snow -> ice,
          melts at {v(cfg, m + '.ice_melt_rate_per_c_h')}/h per °C; wet loads freeze into lumps (+{shares(dig(cfg, m, 'frost', 'penalty'), False)} volume), thaw tau {v(cfg, m + '.frost.thaw_time_constant_h')} h
wet density: rho x (1 + coeff * moisture_extra), coeff {shares(dig(cfg, m, 'wet_density_coeff'), False)}, extra clipped {v(cfg, m + '.moisture_extra_clip')}""")
            + why("moisture is the reason a fill-based estimate drifts with the weather: water adds kilograms without adding "
                  "volume and makes fibre pack denser and softer."))


# ---------------------------------------------------------------- 5.5 compaction
def compaction(cfg: dict) -> str:
    c = "compaction"
    return (h3("Compaction and container state", "sim-compaction") + pre(f"""
layers   one per loading day since the last lift (max {v(cfg, c + '.n_layers')}); each stores base mass, water by pathway,
         reference volume vref, frost volume and pmax = the highest pack pressure it has seen
density  rho = rho_typ * machine_factor * daily_factor * (1 + a*moisture_extra) * g(pmax)
         g(P) = (1 + c*ln(1 + P/{v(cfg, c + '.p0_bar')})) / (1 + c*ln(1 + {v(cfg, c + '.p_ref_bar')}/{v(cfg, c + '.p0_bar')}))      (log-law, plastic: a layer only gets denser)
         machine_factor: lognormal cv {v(cfg, c + '.machine_factor_cv')} * (force/{v(cfg, c + '.force_ref_t')} t)^{v(cfg, c + '.force_exponent')};  daily_factor cv {v(cfg, c + '.daily_factor_cv')}
         compressibility c: {shares(dig(cfg, c, 'compressibility'), False)}
springback right after a stroke the pack is {shares(dig(cfg, c, 'springback'), False)} denser, relaxes with tau {v(cfg, c + '.springback_time_constant_h')} h
fill     = sum(layer volume) / container volume;  material that does not fit -> side waste (true fill never > 100 %)
chamber  loads in an hour without a stroke wait in the press chamber and go in with the next stroke""")
            + p("The mass ledger <code>start + material in + hopper precipitation − drained − seeped − side waste − lifted = end</code> "
                "is checked per machine.")
            + why("the hidden per-machine density factor and the daily scatter are exactly what a fill reading cannot see and "
                  "what the pressure has to reveal."))


# ---------------------------------------------------------------- 5.6 hydraulics
def hydraulics(cfg: dict, b: Build, run: Path, sim_pngs: dict[str, Path]) -> str:
    y, cv = "hydraulics", "hydraulics.curve"
    behaviour = raw_table(["machine behaviour", "simulator"], [
        ["back pressure is visible from <strong>5–10 % fill</strong>, then it rises until full",
         f"<code>fill_onset</code> {v(cfg, y + '.fill_onset')}, <code>fill_exponent</code> {v(cfg, y + '.fill_exponent')} (linear)"],
        ["the ram enters only <strong>20–30 cm</strong> into the container",
         f"pressure graph rises only in the last part of the stroke (<code>ram_ingress_frac</code> {v(cfg, cv + '.ram_ingress_frac')})"],
        ["fill tells <strong>volume, not weight</strong>",
         f"medium density dependence, <code>density_exponent</code> {v(cfg, y + '.density_exponent')}"],
        ["single strokes spike — a <strong>box crushed</strong> on the way in",
         f"spikes +{v(cfg, y + '.spikes.bar')} bar with probability {shares(dig(cfg, y, 'spikes', 'prob_per_stroke'), False)} per stroke"],
        ["the machine can log the <strong>pressure graph</strong>", f"{v(cfg, cv + '.n_points')} samples per stroke → per-stroke curve features"],
    ], left_cols={0, 1}, caption="Machine behaviour and its implementation.")
    imgs = [sim_pngs[s] for s in SHOWN_IN_HYDRAULICS if s in sim_pngs]
    extra = pressure_examples(b, run, cfg)
    return (h3("Hydraulics and the pressure signal", "sim-hydraulics")
            + p("The ram pressure is the only signal that sees the pack. Its law follows how the machine behaves; "
                "the parameter <em>values</em> are assumed, the real ones are unknown.") + behaviour
            + pre(f"""
f(fill)  = clip((fill - {v(cfg, y + '.fill_onset')}) / (1 - {v(cfg, y + '.fill_onset')}), 0, 1)^{v(cfg, y + '.fill_exponent')}
demand   = k * f(fill) * stiffness * (rho/rho_typ)^{v(cfg, y + '.density_exponent')} * soft(moisture) * (1 + {v(cfg, y + '.frost_stiffening')}*frost_share)
           k = {v(cfg, y + '.k_bar')} bar (machine cv {v(cfg, y + '.k_machine_cv')}), stiffness {shares(dig(cfg, y, 'stiffness'), False)}
           soft = clip(1 - wet_softening*moisture_extra, 0.3, 1.5), wet_softening {shares(dig(cfg, y, 'wet_softening'), False)}
P_stroke = min(relief, visc(T_oil) * (P_idle + demand * resistance_noise)) + crushed-item spike
           P_idle U{v(cfg, y + '.p_idle_bar')} bar, relief {v(cfg, y + '.relief_bar')} bar (cv {v(cfg, y + '.relief_cv')}), resistance cv {v(cfg, y + '.stroke_resistance_cv')}
visc     = 1 + {v(cfg, y + '.viscosity_coeff_per_c')} * max({v(cfg, y + '.viscosity_ref_c')} - T_oil, 0)       (cold oil reads high)
T_oil    : +{v(cfg, y + '.oil.heat_per_stroke_c')} °C per stroke, cools to ambient with tau {v(cfg, y + '.oil.cool_time_constant_h')} h, max {v(cfg, y + '.oil.max_c')} °C
P_pack   = min(demand, relief/visc - P_idle)    -> the layer's pmax; density growth stalls once the valve opens""")
            + h4("The pressure graph (per stroke)") + pre(f"""
x        = ram position, 0 = stroke start, 1 = end; {v(cfg, cv + '.n_points')} samples
x_c      = 1 - {v(cfg, cv + '.ram_ingress_frac')} - {v(cfg, cv + '.contact_span')} * clip((fill - {v(cfg, cv + '.contact_onset')})/(1 - {v(cfg, cv + '.contact_onset')}), 0, 1)  +/- {v(cfg, cv + '.contact_noise')}   (contact point)
P(x)     = min(relief, visc * (P_idle + excess * ramp(x)^q + loose(x))) + spike bump
ramp     = clip((x - x_c)/(1 - x_c), 0, 1);  q = {v(cfg, cv + '.shape_base')} + {v(cfg, cv + '.shape_gain')}*(0.35 - c);  loose material up to {v(cfg, cv + '.loose_bar_max')} bar before contact
device   : contact_pos  = first x where P rises > {v(cfg, cv + '.contact_threshold_bar')} bar above the start   (where the pack face is)
           rise_slope   = (P_end - P_contact) / (1 - contact_pos)                 (stiffer / denser pack rises faster)
           mean_bar     = mean pressure over the stroke                             (proportional to hydraulic work)
           completion   = share of the travel done before the relief valve opened""")
            + p("The end-of-stroke value equals the stroke peak, so the curve is consistent with <code>peak_pressure_bar</code>. "
                "Hourly means of the four device features become the <code>curve_*</code> columns; raw curves are kept for the "
                f"first {v(cfg, cv + '.sample_bins')} machines.")
            + figures(imgs + extra)
            + why("pressure is what separates a light from a heavy container at the same fill; making its dependence on "
                  "density only medium keeps the problem honest: the fill reading tells volume, not weight."))


def _hourly_cols(run: Path, cols: list[str], bins: list[str] | None = None) -> pd.DataFrame | None:
    path = ROOT / run / "hourly.parquet"
    if not path.exists():
        return None
    filters = [("bin_id", "in", bins)] if bins else None
    return pd.read_parquet(path, columns=cols, filters=filters)


def pressure_examples(b: Build, run: Path, cfg: dict) -> list[Path]:
    """Example pressure graphs at low / medium / high true fill -> site/img/gen/pressure_examples.png."""
    src = b.check(run / "pressure_curves.parquet")
    if src is None:
        return []
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        curves = pd.read_parquet(src)
        fill = _hourly_cols(run, ["bin_id", "ts", "true_fill_pct"], sorted(curves["bin_id"].unique().tolist()))
        curves = curves.assign(hour=curves["ts"].dt.floor("h")).merge(fill.rename(columns={"ts": "hour"}), on=["bin_id", "hour"], how="left")
        thr = float(dig(cfg, "hydraulics", "curve", "contact_threshold_bar") or 15.0)
        rng = np.random.default_rng(7)
        fig, ax = plt.subplots(figsize=(8, 4.5))
        for lo, hi, label, color in FILL_BANDS:
            keys = curves[(curves["true_fill_pct"] >= lo) & (curves["true_fill_pct"] < hi)][["bin_id", "ts", "stroke_in_hour"]].drop_duplicates()
            if keys.empty:
                continue
            chosen = keys.iloc[rng.choice(len(keys), min(4, len(keys)), replace=False)]
            for i, key in enumerate(chosen.itertuples(index=False)):
                one = curves[(curves["bin_id"] == key.bin_id) & (curves["ts"] == key.ts)
                             & (curves["stroke_in_hour"] == key.stroke_in_hour)].sort_values("ram_position")
                x, pr_ = one["ram_position"].to_numpy(), one["pressure_bar"].to_numpy()
                ax.plot(x, pr_, color=color, lw=1.4, alpha=0.85, label=label if i == 0 else None)
                rise = np.flatnonzero(pr_ - pr_[0] > thr)
                if rise.size:
                    ax.plot(x[rise[0]], pr_[rise[0]], "o", color=color, ms=5, mec="black", mew=0.5)
        ax.set(xlabel="ram position (0 = start, 1 = end of stroke)", ylabel="pressure (bar)",
               title="Example pressure graphs by true fill (dot = detected contact point)")
        ax.legend(title="true fill", loc="upper left")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        out = save_figure(fig, GEN / "pressure_examples.png", GEN_DPI)
        plt.close(fig)
        return out
    except (OSError, ValueError, KeyError) as exc:
        logging.warning("pressure example plot failed: %s", exc)
        return []


# ---------------------------------------------------------------- 5.7 sensors
def fill_error(b: Build, run: Path) -> tuple[str, list[Path]]:
    """Mean |fill_pct - true_fill_pct| from the run (stored in b.facts), plus a hexbin plot."""
    b.check(run / "hourly.parquet")
    try:
        h = _hourly_cols(run, ["fill_pct", "true_fill_pct"])
        if h is None:
            return "", []
        h = h.dropna()
        err = (h["fill_pct"] - h["true_fill_pct"]).abs()
        hi = err[h["true_fill_pct"] >= 30]
        b.facts.update(fill_err_pp=float(err.mean()), fill_err_hi_pp=float(hi.mean()), fill_readings=int(len(h)))
        text = p(f"Measured on the 400-machine run ({num(len(h), 0)} hourly readings): mean |fill_pct − true fill| = "
                 f"<strong>{num(err.mean())} pp</strong> overall, {num(hi.mean())} pp at true fill ≥ 30 % (including the "
                 "spurious-high episodes). The reference accuracy is about 5 %.")
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        sample = h.sample(min(len(h), 400_000), random_state=7)
        fig, ax = plt.subplots(figsize=(5.6, 4.5))
        hb = ax.hexbin(sample["true_fill_pct"], sample["fill_pct"], gridsize=50, bins="log", cmap="viridis", mincnt=1)
        ax.plot([0, 100], [0, 100], color="white", lw=1, ls="--")
        fig.colorbar(hb, ax=ax, label="hours (log)")
        ax.set(xlabel="true fill (%)", ylabel="reported fill_pct (%)", title="Firmware fill estimate vs truth")
        fig.tight_layout()
        out = save_figure(fig, GEN / "fill_estimate.png", GEN_DPI)
        plt.close(fig)
        return text, out
    except (OSError, ValueError, KeyError) as exc:
        logging.warning("fill error stats failed: %s", exc)
        return "", []


def sensors(cfg: dict, b: Build, run: Path) -> str:
    s = "sensors"
    text, imgs = fill_error(b, run)
    outage = ", ".join(str(x) for x in dig(cfg, "sensors", "outage", "duration_h_lognormal") or [])
    return (h3("Sensors", "sim-sensors")
            + p(f"<strong>The fill level is not measured directly.</strong> With <code>fill_mode</code> "
                f"<code>{v(cfg, s + '.fill_mode')}</code> the firmware estimates it from the back pressure, like a real "
                "Europress-style machine without a separate level sensor:")
            + pre(f"""
P_avg   = exponential rolling average over ~{v(cfg, s + '.pressure_fill.rolling_strokes')} strokes (alpha = 2/(n+1) per stroke), reset at a lift
fill %  = 100 * clip((P_avg - P_idle) / P_full, 0, 1)        no oil-temperature correction (compensate_oil_temp: {v(cfg, s + '.pressure_fill.compensate_oil_temp')})
P_full  = {v(cfg, s + '.pressure_fill.full_backpressure_bar')} bar * material stiffness, calibrated {v(cfg, s + '.pressure_fill.calibration')} (machine scale, idle, typical density)
spurious high: {v(cfg, s + '.false_full.rate_per_day')} per day, {v(cfg, s + '.false_full.duration_days')} days at {v(cfg, s + '.false_full.level_pct')} %, only once true fill >= {v(cfg, s + '.false_full.min_true_fill_pct')} %, clears at lift
dropouts: {v(cfg, s + '.dropout_prob')} of hours + outages {v(cfg, s + '.outage.rate_per_day')}/day, duration lognormal(mu, sigma = {escape(outage)}) h (max {v(cfg, s + '.outage.max_h')} h)
pressure transducer: noise {v(cfg, s + '.pressure.noise_sd_bar')} bar, {v(cfg, s + '.pressure.quantum_bar')} bar steps, gain bias sd {v(cfg, s + '.pressure.gain_bias_sd')}, drift sd {v(cfg, s + '.pressure.gain_drift_sd_per_year')}/yr""")
            + p("So cold oil reads fuller, and day-to-day density scatter and crushed-item spikes leak into the reading.") + text
            + figures(imgs, single=True)
            + why("the model's main input is itself an estimate derived from pressure; pretending it is a clean level sensor "
                  "would overstate what fill alone can do."))


# ---------------------------------------------------------------- 5.8 operations
def operations(cfg: dict) -> str:
    o = "operations"
    return (h3("Operations", "sim-ops") + pre(f"""
order    when the observed fill >= threshold (80/85/90 %), or {v(cfg, o + '.scheduled_lead_h')} h before the max interval of {v(cfg, o + '.max_interval_days')} days
truck    arrives {v(cfg, o + '.truck_delay_h')} h after the order, only {v(cfg, o + '.truck_hours')} h local, no Sundays: {v(cfg, o + '.no_sunday')}, no holidays: {v(cfg, o + '.no_holidays')}
lift     COMBI: press + container lifted and emptied fully; DUO: container swapped, residue U{v(cfg, o + '.duo_residue_frac')} of the layers
         the final stroke before the swap also takes the chamber contents
weighbridge  net kg with resolution {v(cfg, o + '.weighbridge.resolution_kg')} kg and noise sd {v(cfg, o + '.weighbridge.noise_sd_kg')} kg""")
            + p("The lift happens at the start of the <code>lift_event</code> hour; the row one hour earlier holds the mass "
                "that was lifted — that is the value compared with the weighbridge.")
            + why("false-full readings and truck delays make the container's fill at lift vary widely, which is what the soft "
                  "sensor has to get right."))


# ---------------------------------------------------------------- 5.9 validation
KEY_LINES = (("mass balance", r"mass-balance max"), ("heaviest lift", r"heaviest net lift"),
             ("days between lifts", r"days between lifts"), ("overflow", r"^overflow"),
             ("peak pressure", r"^peak pressure:"), ("pressure at high fill", r"peak pressure at true fill"),
             ("pressure graph", r"corr\(contact position"), ("weather effect", r"open hopper heavier"),
             ("false full", r"share of lifts triggered"), ("false full", r"mean true fill before lift"))


def _sanitise(text: str) -> str:
    """Remove file-system locations from the validation report before showing it."""
    text = re.sub(r"^Run \S+:", "Run:", text, flags=re.M)
    text = re.sub(r"^plots written to .*$", "", text, flags=re.M)
    return re.sub(r"\S*(?:runs|models|home)/\S+", "the run", text).rstrip() + "\n"


def validation(b: Build, run: Path, summary: dict | None, sim_pngs: dict[str, Path], report_href: str | None) -> str:
    parts = [h3("Validation of the simulator", "sim-validation")]
    if summary:
        lm = summary.get("lift_mass_by_material") or {}
        rows = [[m, num((dig(x, "mean") or float("nan")) / 1000, 2), LIFT_TARGET_T.get(m, "–"), num(dig(x, "count"), 0)] for m, x in lm.items()]
        parts.append(table(["material", "mean lift (t)", "brief target (t)", "lifts"], rows, caption="Mean lifted mass per material against the brief's targets."))
        parts.append(p(f"False-full-triggered lifts: <strong>{pct_of(summary.get('false_full_lift_share'))} %</strong>. "
                       f"Largest per-machine mass-balance residual: {sci(summary.get('mass_balance_max_abs_residual_kg'))} kg."))
        mb = summary.get("mass_balance_kg") or {}
        parts.append(table(["term", "kg"], [[k.removesuffix("_kg"), sci(x) if k == "residual_kg" else num(x, 0)] for k, x in mb.items()],
                           caption="Mass balance of the whole fleet over the reported period."))
    src = b.check(run / "validate_report.txt")
    if src is not None:
        text = src.read_text()
        lines = text.splitlines()
        rows = []
        for label, pat in KEY_LINES:
            hit = next((ln.strip() for ln in lines if re.search(pat, ln.strip())), None)
            if hit:
                rows.append([label, hit])
        corr = [ln.strip() for ln in lines if re.match(r"\s+\w+\s+r = ", ln)]
        if corr:
            rows.append(["corr(pressure, density) at fill 85–100 %", " · ".join(corr)])
        m = re.search(r"corr\(contact position, true fill\) (-?[\d.]+)", text)
        if m:
            b.facts["contact_corr"] = float(m.group(1))
        link = f' The complete report is also available as a <a href="{escape(report_href)}">download</a>.' if report_href else ""
        parts.append(table(["check", "report line"], rows, left_cols={0, 1}, caption="Key lines of the validation report.")
                     + p("The full validation report of the run, as written by the validation script:" + link) + pre(_sanitise(text)))
    else:
        parts.append(note("Validation report not available at build time."))
    rest = [pth for s, pth in sim_pngs.items() if s not in SHOWN_IN_HYDRAULICS]
    parts.append(h4("Validation plots") + figures(rest))
    return "".join(parts)


def simulation_detail(b: Build, run: Path, summary: dict | None, cfg: dict | None, sim_pngs: dict[str, Path],
                      report_href: str | None) -> str:
    head = ('<section id="sec-sim">' + h2("Simulation: how the data was generated", "sim")
            + p("The simulator generates hourly telemetry and ground truth for a fleet of press containers. Everything below "
                "describes the 400-machine run used throughout this document; parameter values are read from the configuration "
                "stored with the run. Formulas are simplified from the code. Values marked 'assumed' were chosen by us and are "
                "not validated against real data; the hydraulic parameters in particular are not real Europress values."))
    if not cfg:
        return head + note("No simulator configuration available.") + "</section>"
    return (head + fleet(cfg) + weather(cfg) + inflow(cfg) + moisture(cfg) + compaction(cfg)
            + hydraulics(cfg, b, run, sim_pngs) + sensors(cfg, b, run) + operations(cfg)
            + validation(b, run, summary, sim_pngs, report_href) + "</section>")


__all__ = ["simulation_detail"]
