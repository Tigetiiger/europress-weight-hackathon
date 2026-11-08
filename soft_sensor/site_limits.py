"""Section 6: limitations and caveats, with the numbers read from the run and the evaluation metrics."""
from __future__ import annotations

import logging
from html import escape
from pathlib import Path

import pandas as pd

from site_common import ROOT, SPLIT_LABEL, Build, dig, h2, h3, num, p, pct_of, table


def _li(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"


def heavy_lifts(b: Build, sim_run: Path) -> tuple[str, list[list[str]]]:
    src = b.check(sim_run / "lifts.parquet")
    if src is None:
        return "", []
    try:
        lifts = pd.read_parquet(src, columns=["true_mass_removed_kg", "material", "equipment_class", "container_volume_m3",
                                              "days_since_last_lift", "side_waste_since_last_kg", "true_fill_before_pct"])
    except (OSError, ValueError) as exc:
        logging.warning("cannot read lifts: %s", exc)
        return "", []
    big = lifts[lifts["true_mass_removed_kg"] > 10_000]
    groups = big.groupby(["material", "equipment_class", "container_volume_m3"]).size()
    where = "; ".join(f"{m} in {num(vol, 0)} m³ {c} ({n})" for (m, c, vol), n in groups.items())
    b.facts.update(n_heavy=int(len(big)), max_lift_kg=float(lifts["true_mass_removed_kg"].max()))
    text = (f"<strong>Heaviest lifts above 10 t.</strong> {len(big)} of {num(len(lifts), 0)} lifts removed more than 10 t "
            f"(maximum {num(lifts['true_mass_removed_kg'].max() / 1000, 1)} t net), all {escape(where)}. A hook-lift truck "
            "carries about 10 t, so these lifts would not happen in reality; mixed waste at 400 kg/m³ should be restricted to "
            "containers of at most 14–17 m³. The soft sensor is unaffected (it predicts kg/m³), but the mixed-waste mass range "
            "of the data is wider than a real fleet's.")
    rows = [["lifts with some side waste since the previous lift", f"{pct_of((lifts['side_waste_since_last_kg'] > 0).mean())} %"],
            ["lifts that found the container at ≥ 99 % true fill", f"{pct_of((lifts['true_fill_before_pct'] >= 99).mean())} %"],
            ["days between lifts: median / 5th / 95th percentile",
             " / ".join(num(lifts["days_since_last_lift"].quantile(q), 1) for q in (0.5, 0.05, 0.95))]]
    return text, rows


def weather_year(b: Build, sim_run: Path, cfg: dict | None, summary: dict | None) -> str:
    src = b.check(sim_run / "weather_daily.parquet")
    if src is None or not summary:
        return ""
    try:
        w = pd.read_parquet(src, columns=["region", "date", "precip_mm"])
    except (OSError, ValueError) as exc:
        logging.warning("cannot read weather: %s", exc)
        return ""
    start = pd.Timestamp(summary.get("start"))
    w = w[pd.to_datetime(w["date"]) >= start]
    tot = w.groupby("region")["precip_mm"].sum()
    parts = []
    for r, mm in tot.items():
        target = dig(cfg, "weather", "regions", r, "precip_mm_year")
        parts.append(f"{r} {num(mm, 0)} mm (target {target})")
    return (f"<strong>One synthetic weather year.</strong> The data cover a single year ({start:%B %Y} to "
            f"{(start + pd.Timedelta(days=int(summary.get('days', 365)) - 1)):%B %Y}) of one weather draw. Annual precipitation "
            f"of that draw: {escape(', '.join(parts))}. A single year has an interannual spread of about 12 %, like real "
            "Finnish data; the generator is unbiased over seeds, but the model has seen only this one year, and there is no "
            "microclimate, wind-driven rain or roof overhang.")


def container_sizes(b: Build, sim_run: Path) -> str:
    src = b.check(sim_run / "bins.parquet")
    if src is None:
        return ""
    try:
        bins = pd.read_parquet(src, columns=["material", "equipment_class", "container_volume_m3"])
    except (OSError, ValueError) as exc:
        logging.warning("cannot read bins: %s", exc)
        return ""
    desc = []
    for m, g in bins.groupby("material"):
        sizes = sorted(g["container_volume_m3"].unique())
        desc.append(f"{m}: {len(g)} machines on {', '.join(num(s, 0) for s in sizes)} m³")
    return (f"<strong>Uneven coverage of materials and container sizes.</strong> {escape('; '.join(desc))}. Paper and mixed "
            "waste are rare and appear on few container sizes, so the unseen-machines results for them rest on small test "
            "sets and the model has not been tested on sizes outside this mix.")


def model_caveats(metrics: dict | None, summary: dict | None, cfg: dict | None, facts: dict) -> list[str]:
    bins_ = dig(metrics, "splits", "bins") or {}
    time_ = dig(metrics, "splits", "time") or {}
    items = []
    cov = dig(bins_, "final", "lift", "by_material", "mixed_waste", "coverage")
    if cov is not None:
        items.append(f"<strong>Interval coverage of mixed waste on unseen machines is {pct_of(cov, 0)} %</strong> against the "
                     f"80 % target (hourly: {pct_of(dig(bins_, 'final', 'hourly', 'by_material', 'mixed_waste', 'coverage'), 0)} %). "
                     f"Only {dig(bins_, 'n_test_machines')} test machines are held out in that split and mixed waste is 10 % of "
                     "the fleet, so the calibration slice for this material is small, and its error scale does not transfer to "
                     "the test machines. All other material × split cells are within a few points of the target.")
    ts = dig(bins_, "test_start") or dig(time_, "test_start")
    if ts and summary:
        end = pd.Timestamp(summary.get("start")) + pd.Timedelta(days=int(summary.get("days", 365)) - 1)
        items.append(f"<strong>The test period is one season.</strong> Both splits test on {escape(str(ts)[:10])} to "
                     f"{end:%Y-%m-%d}: summer and early autumn. Winter behaviour (frost, ice, cold oil, snow in the hopper) is in "
                     "the training data but has not been tested on held-out data.")
    wb = dig(cfg, "operations", "weighbridge") or {}
    items.append(f"<strong>Evaluation is against the true mass, not against a weighbridge reading.</strong> A real comparison "
                 f"adds the weighbridge's own error (in the simulator {wb.get('resolution_kg', '–')} kg steps and "
                 f"{wb.get('noise_sd_kg', '–')} kg noise, about 0.5 % of a typical lift) and, for DUO, the uncertainty of the tare.")
    bias_t, bias_u = dig(time_, "final", "lift", "overall", "bias_kg"), dig(bins_, "final", "lift", "overall", "bias_kg")
    if bias_t is not None:
        items.append(f"<strong>Small negative bias.</strong> The estimate at lift is on average {num(bias_t, 0)} kg "
                     f"({SPLIT_LABEL['time']}) and {num(bias_u, 0)} kg ({SPLIT_LABEL['bins']}) below the true mass. A plausible reason, not "
                     "verified here, is that the fullest hour of a cycle is where the pressure saturates at the relief valve "
                     "and where the training data are thinnest.")
    ff = dig(time_, "breakdowns", "false_full_in_cycle") or {}
    if ff:
        items.append(f"<strong>False-full episodes hurt.</strong> {pct_of((summary or {}).get('false_full_lift_share'))} % of all "
                     f"lifts are triggered by a spurious-high fill reading; lifts whose cycle contained such an episode have a mean "
                     f"error of {num(dig(ff, 'yes', 'mean_pct'))} % versus {num(dig(ff, 'no', 'mean_pct'))} % without "
                     f"({SPLIT_LABEL['time']}; {num(dig(bins_, 'breakdowns', 'false_full_in_cycle', 'yes', 'mean_pct'))} % versus "
                     f"{num(dig(bins_, 'breakdowns', 'false_full_in_cycle', 'no', 'mean_pct'))} % on unseen machines).")
    items += [
        "<strong>The first cycle of every machine is excluded</strong> from training and testing because it is incomplete; "
        "a freshly installed machine therefore has no estimate until its first lift, and the features that count 'since lift' "
        "are undefined before it.",
        "<strong>The pressure graph and the per-stroke log are assumed to be available</strong> for every machine. On the "
        "simulated fleet the hourly curve features exist for all machines; raw curves are kept for five. If a real machine "
        "logs only the hourly peak pressure, the ladder shows what is lost (Section 3.2).",
        "<strong>Hyper-parameters come from a four-point grid</strong> and a single training seed. The spread of the learning "
        "curve over machine draws (Sections 3.6 and 4) is the only variance estimate reported.",
        f"<strong>The reference load cells</strong> on {pct_of(dig(cfg, 'fleet', 'ref_scale_share'), 0)} % of the simulated "
        "machines are not used; a real deployment would use them to calibrate and monitor the soft sensor.",
    ]
    return items


def data_caveats(facts: dict, cfg: dict | None) -> list[str]:
    items = []
    if "fill_err_pp" in facts:
        items.append(f"<strong>The fill reading is less accurate than quoted.</strong> The simulated firmware estimate is "
                     f"{num(facts['fill_err_pp'])} pp from the true fill on average ({num(facts.get('fill_err_hi_pp'))} pp above "
                     "30 % fill), against a reference accuracy of roughly 5 %. This is a property of the simulated calibration "
                     "(per-installation setpoint, no oil-temperature compensation) and makes the fill features somewhat weaker "
                     "than on a real machine if the quote is right.")
    if "contact_corr" in facts:
        items.append(f"<strong>The contact point of the pressure graph is only weakly informative.</strong> Its correlation with "
                     f"the true fill is {num(facts['contact_corr'], 2)} at fill ≥ 35 %, because the ram enters only 20–30 cm into "
                     f"the container (ingress fraction {dig(cfg, 'hydraulics', 'curve', 'ram_ingress_frac')}) and loose material "
                     "blurs the onset. The rise slope, not the contact point, is what the models use (Section 2.5).")
    return items


def sim_caveats() -> list[str]:
    return [
        "Weather is regional (5 points), without microclimate, wind-driven rain, roof overhangs or hopper canopies.",
        "The pressure law is phenomenological and its parameters are not real Europress values. There is no position or "
        "stroke-length signal, no partial strokes and no pack friction along the container.",
        "All layers share the pack pressure of each stroke: no pressure gradient front-to-back, no creep beyond springback.",
        "Each machine is its own site. Co-located machines and shared staff behaviour are not modelled; side waste leaves the "
        "system instead of being reloaded after the lift.",
        "Hygroscopic moisture is fixed at loading; the closed container does not exchange moisture afterwards, and pooled water "
        "does not soak into the bottom layers.",
        "Truck logistics are simple (independent random delay, no route capacity). Lifts during telemetry outages still happen.",
        "The true-fill ceiling differs slightly by material because of springback (about 93–98 % right after a stroke).",
        "With the default cycle lengths no site reaches the 28-day maximum interval, so scheduled lifts of part-full containers "
        "are almost absent from the data.",
    ]


def limitations(b: Build, metrics: dict | None, summary: dict | None, cfg: dict | None, sim_run: Path) -> str:
    head = '<section id="sec-limits">' + h2("Limitations and caveats", "limits")
    heavy, lift_rows = heavy_lifts(b, sim_run)
    data_items = [x for x in (heavy, weather_year(b, sim_run, cfg, summary), container_sizes(b, sim_run)) if x] + data_caveats(b.facts, cfg)
    return (head
            + h3("Synthetic data and assumed parameters", "lim-synthetic")
            + p("Every number in this document comes from simulated data. The simulator (Section 5) was built from the hackathon "
                "brief and public product information; many of its parameters are marked 'assumed' and "
                "none of its hydraulic settings are real Europress values. Results therefore show what the method can do when "
                "the physics behaves as modelled, not what it will do in the field. A real pilot with weighbridge tickets is "
                "needed before any of the error figures can be trusted.")
            + h3("Known issues in the generated data", "lim-data") + _li(data_items)
            + (table(["check", "value"], lift_rows, left_cols={0, 1}, caption="Lift statistics of the run relevant to the caveats above.") if lift_rows else "")
            + h3("Caveats of the model and its evaluation", "lim-model") + _li(model_caveats(metrics, summary, cfg, b.facts))
            + h3("Simulator limitations", "lim-sim")
            + p("Things the simulator does not model, which could change the picture on real machines:") + _li([escape(x) for x in sim_caveats()])
            + "</section>")
