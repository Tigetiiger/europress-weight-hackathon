"""Sanity checks for a simulator run: prints a report and writes plots.

    python validate.py runs/<name>            # plots go to runs/<name>/plots/
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

LIFT_TARGETS_KG = {"cardboard": (2000, 3500), "paper": (2500, 4000),
                   "plastic_film": (1200, 2200), "mixed_waste": (3000, 6000)}  # mixed_waste range assumed
PRECIP_TARGETS_MM = {"UUS": (600, 700), "VAR": (600, 700), "PIR": (550, 700),
                     "POP": (450, 550), "LAP": (450, 550)}
TRUCK_LIMIT_KG = 10_000
SIDE_WASTE_LIMIT = 0.06  # side waste above this share of inflow means overflow is no longer the exception
MATERIAL_COLORS = {"cardboard": "#b07c3c", "paper": "#4c78a8", "plastic_film": "#54a24b", "mixed_waste": "#e45756"}


def status(ok: bool) -> str:
    return "OK  " if ok else "WARN"


def load_run(run: Path) -> dict[str, pd.DataFrame]:
    cols = ["bin_id", "ts", "material", "infeed", "fill_pct", "presses", "peak_pressure_bar",
            "true_fill_pct", "true_density_kg_m3", "true_mass_kg", "oil_temp_c", "lift_event"]
    import pyarrow.parquet as pq
    names = pq.read_schema(run / "hourly.parquet").names
    cols += [c for c in ("curve_contact_pos", "curve_rise_slope_bar") if c in names]
    return {
        "lifts": pd.read_parquet(run / "lifts.parquet"),
        "bins": pd.read_parquet(run / "bins.parquet"),
        "weather": pd.read_parquet(run / "weather_daily.parquet"),
        "hourly": pd.read_parquet(run / "hourly.parquet", columns=cols),
        "summary": json.loads((run / "summary.json").read_text()),
    }


# ----------------------------------------------------------------------------- checks
def check_lifts(d, plots: Path) -> None:
    lifts = d["lifts"]
    print("\n== Lifted mass per material (true net kg) ==")
    print(f"{'material':15s} {'lifts':>6s} {'mean':>7s} {'p10':>7s} {'p90':>7s}  target")
    for mat, (lo, hi) in LIFT_TARGETS_KG.items():
        m = lifts.loc[lifts["material"] == mat, "true_mass_removed_kg"]
        ok = lo <= m.mean() <= hi
        print(f"{mat:15s} {len(m):6d} {m.mean():7.0f} {m.quantile(.1):7.0f} {m.quantile(.9):7.0f}  "
              f"{lo}-{hi}  {status(ok)}")
    heaviest = lifts["true_mass_removed_kg"].max()
    gross = lifts["gross_vehicle_load_kg"].max()
    print(f"heaviest net lift {heaviest:.0f} kg (limit ~{TRUCK_LIMIT_KG}) {status(heaviest < TRUCK_LIMIT_KG)}; "
          f"heaviest incl. container tare {gross:.0f} kg")
    days = lifts["days_since_last_lift"]
    share = ((days >= 2) & (days <= 14)).mean()
    print(f"days between lifts: median {days.median():.1f}, p5 {days.quantile(.05):.1f}, "
          f"p95 {days.quantile(.95):.1f}; share in 2-14 d = {share:.0%} {status(share > 0.7)}")
    print("lift trigger mix:", lifts["trigger"].value_counts(normalize=True).round(3).to_dict())
    _print_overflow(lifts, d["summary"]["mass_balance_kg"])
    _plot_lifts(lifts, plots)


def _print_overflow(lifts: pd.DataFrame, balance: dict) -> None:
    """Side waste (material that did not fit) and how often a lift found the container at the ceiling."""
    side_share = balance["side_waste_kg"] / balance["material_in_kg"]
    lifts_with_side = (lifts["side_waste_since_last_kg"] > 0).mean()
    lifts_at_full = (lifts["true_fill_before_pct"] >= 99).mean()
    print(f"overflow: side waste {side_share:.1%} of inflow (limit {SIDE_WASTE_LIMIT:.0%}); "
          f"lifts with side waste {lifts_with_side:.0%}; lifts with true fill >= 99 % before lift "
          f"{lifts_at_full:.0%} {status(side_share <= SIDE_WASTE_LIMIT)}")


def _plot_lifts(lifts: pd.DataFrame, plots: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for mat, color in MATERIAL_COLORS.items():
        m = lifts.loc[lifts["material"] == mat, "true_mass_removed_kg"] / 1000
        axes[0].hist(m, bins=60, alpha=0.6, label=mat, color=color)
    axes[0].set(xlabel="lifted mass (t)", ylabel="lifts", title="Lifted mass per material")
    axes[0].legend()
    axes[1].hist(lifts["days_since_last_lift"].clip(upper=30), bins=60, color="#4c78a8")
    axes[1].set(xlabel="days since previous lift", ylabel="lifts", title="Days between lifts")
    fig.tight_layout()
    fig.savefig(plots / "lifts.png", dpi=110)
    plt.close(fig)


def check_weather(d, plots: Path, start: str) -> None:
    w = d["weather"].copy()
    w["date"] = pd.to_datetime(w["date"])
    w = w[w["date"] >= pd.Timestamp(start)]
    years = w["date"].nunique() / 365.25
    print("\n== Weather per region (reported period) ==")
    print(f"{'region':6s} {'precip/yr':>9s} {'wet>=0.1':>8s} {'wet>=1':>7s} {'snow/yr':>8s} {'Jan T':>6s} {'Jul T':>6s} {'mean T':>6s}  target")
    monthly = w.assign(month=w["date"].dt.month).groupby(["region", "month"])["temp_mean_c"].mean().unstack()
    for region, g in w.groupby("region"):
        precip = g["precip_mm"].sum() / years
        lo, hi = PRECIP_TARGETS_MM.get(region, (0, 9999))
        jan = monthly.loc[region].get(1, np.nan)
        jul = monthly.loc[region].get(7, np.nan)
        print(f"{region:6s} {precip:9.0f} {(g['precip_mm'] >= 0.1).mean():8.0%} {(g['precip_mm'] >= 1).mean():7.0%} "
              f"{g['snow_mm'].sum() / years:8.0f} {jan:6.1f} {jul:6.1f} {g['temp_mean_c'].mean():6.1f}  "
              f"{lo}-{hi} mm {status(lo <= precip <= hi)}")
    print("  note: one simulated year has an interannual sd of ~12 % (like real Finnish data); the generator's\n"
          "        30-seed mean is within 1-4 % of config precip_mm_year, so single-year WARNs are weather, not bias.")
    print("monthly mean temperature (C):")
    print(monthly.round(1).to_string())
    fig, ax = plt.subplots(figsize=(7, 4))
    for region in monthly.index:
        ax.plot(monthly.columns, monthly.loc[region], marker="o", label=region)
    ax.set(xlabel="month", ylabel="mean temperature (C)", title="Monthly mean temperature")
    ax.legend()
    fig.tight_layout()
    fig.savefig(plots / "temperature.png", dpi=110)
    plt.close(fig)


def _lift_weather(lifts: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    """Rain sum and mean RH over the 7 days before each lift day."""
    w = weather.copy()
    w["date"] = pd.to_datetime(w["date"])
    w = w.sort_values(["region", "date"])
    g = w.groupby("region")
    w["rain7"] = g["precip_mm"].transform(lambda s: s.shift(1).rolling(7).sum())
    w["rh7"] = g["rh_pct"].transform(lambda s: s.shift(1).rolling(7).mean())
    day = lifts["ts"].dt.tz_localize(None).dt.normalize()
    key = lifts.assign(date=day)[["region", "date"]]
    return key.merge(w[["region", "date", "rain7", "rh7"]], on=["region", "date"], how="left")


def check_weather_effect(d, plots: Path) -> None:
    lifts = d["lifts"].copy()
    lw = _lift_weather(lifts, d["weather"])
    lifts["rain7"], lifts["rh7"] = lw["rain7"].to_numpy(), lw["rh7"].to_numpy()
    lifts = lifts[(lifts["material"] == "cardboard") & (lifts["trigger"] == "fill") & ~lifts["false_full_trigger"]]
    lifts = lifts.dropna(subset=["rain7"])
    # mass per m3 of (true) occupied volume at the lift, relative to the machine's own mean
    dens = lifts["true_mass_removed_kg"] / (lifts["container_volume_m3"] * lifts["true_fill_before_pct"] / 100)
    lifts["rel_density"] = dens / dens.groupby(lifts["bin_id"]).transform("mean")
    lifts["rel_mass"] = lifts["true_mass_removed_kg"] / lifts.groupby("bin_id")["true_mass_removed_kg"].transform("mean")
    wet = (lifts["rain7"] >= lifts["rain7"].quantile(0.75)) & (lifts["rh7"] >= lifts["rh7"].median())
    dry = (lifts["rain7"] <= lifts["rain7"].quantile(0.25)) & (lifts["rh7"] <= lifts["rh7"].median())
    print("\n== Weather effect on cardboard lifts (wet & humid week vs dry week, machine-normalised) ==")
    print(f"wet week: rain7 >= {lifts['rain7'].quantile(.75):.0f} mm & RH7 >= {lifts['rh7'].median():.0f} %;  "
          f"dry week: rain7 <= {lifts['rain7'].quantile(.25):.0f} mm & RH7 <= median")
    rows = []
    for infeed in ("open_hopper", "lidded_hopper", "indoor_shaft"):
        sel = lifts["infeed"] == infeed
        w_, d_ = lifts[sel & wet], lifts[sel & dry]
        rows.append((infeed, 100 * (w_["rel_mass"].mean() / d_["rel_mass"].mean() - 1),
                     100 * (w_["rel_density"].mean() / d_["rel_density"].mean() - 1),
                     100 * w_["true_moisture_extra"].mean(), 100 * d_["true_moisture_extra"].mean(), len(w_), len(d_)))
        print(f"{infeed:14s} lift mass {rows[-1][1]:+5.1f} %   kg/m3 {rows[-1][2]:+5.1f} %   moisture extra "
              f"{rows[-1][3]:5.1f} % vs {rows[-1][4]:5.1f} %   (n={len(w_)}/{len(d_)})")
    open_gain, shaft_gain = rows[0][1], rows[2][1]
    print(f"open hopper heavier than dry by {open_gain:.1f} %, indoor shaft by {shaft_gain:.1f} % "
          f"{status(1 <= open_gain <= 12 and open_gain > shaft_gain)} (expected: a few %, open > shaft)")
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar([r[0] for r in rows], [r[1] for r in rows], color=["#4c78a8", "#72b7b2", "#b0b0b0"])
    ax.set(ylabel="lift mass, wet vs dry week (%)", title="Cardboard: weather effect by infeed type")
    fig.tight_layout()
    fig.savefig(plots / "weather_effect.png", dpi=110)
    plt.close(fig)


def check_pressure(d, plots: Path, rng: np.random.Generator) -> None:
    h = d["hourly"]
    p = h.dropna(subset=["peak_pressure_bar"])
    relief = d["bins"]["relief_bar"].max()
    print("\n== Hydraulic pressure ==")
    print(f"peak pressure: p50 {p['peak_pressure_bar'].median():.0f} bar, max {p['peak_pressure_bar'].max():.1f} bar "
          f"(max relief setting {relief:.0f} bar + noise) {status(p['peak_pressure_bar'].max() <= relief * 1.08 + 25)}")
    high = p[p["true_fill_pct"] >= 85]
    print(f"peak pressure at true fill >= 85 %: p10 {high['peak_pressure_bar'].quantile(.1):.0f}, "
          f"p50 {high['peak_pressure_bar'].median():.0f}, p90 {high['peak_pressure_bar'].quantile(.9):.0f} bar "
          f"(target typical 150-220)")
    print("corr(peak pressure, true density) at true fill 85-100 %, per material:")
    for mat, g in high.groupby("material"):
        r = np.corrcoef(g["peak_pressure_bar"], g["true_density_kg_m3"])[0, 1]
        print(f"  {mat:15s} r = {r:+.2f} {status(r > 0.3)}")
    low = p[p["true_fill_pct"] < 30]
    print(f"at true fill < 30 % the pressure is ~idle: p50 {low['peak_pressure_bar'].median():.0f} bar")
    sample = p.iloc[rng.choice(len(p), size=min(len(p), 150_000), replace=False)]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, mats in zip(axes, (["cardboard", "paper"], ["plastic_film", "mixed_waste"])):
        s = sample[sample["material"].isin(mats)]
        sc = ax.scatter(s["true_fill_pct"], s["peak_pressure_bar"], c=s["true_density_kg_m3"], s=1, cmap="viridis",
                        vmin=s["true_density_kg_m3"].quantile(.02), vmax=s["true_density_kg_m3"].quantile(.98))
        ax.set(xlabel="true fill (%)", title=" + ".join(mats))
        fig.colorbar(sc, ax=ax, label="true density (kg/m3)")
    axes[0].set_ylabel("hourly peak pressure (bar)")
    fig.tight_layout()
    fig.savefig(plots / "pressure_vs_fill.png", dpi=110)
    plt.close(fig)


def check_pressure_graph(run: Path, d, plots: Path) -> None:
    """Pressure along the ram stroke: the contact point measures fill, the rise reflects density."""
    path = run / "pressure_curves.parquet"
    h = d["hourly"]
    if "curve_contact_pos" not in h or not path.is_file():
        print("\n== Pressure graph == not in this run")
        return
    g = h.dropna(subset=["curve_contact_pos", "fill_pct"])
    g = g[g["true_fill_pct"] >= 35]
    print("\n== Pressure graph (fill >= 35 %) ==")
    print(f"corr(contact position, true fill) {np.corrcoef(g['curve_contact_pos'], g['true_fill_pct'])[0, 1]:+.2f}; "
          f"corr(pressure-derived fill_pct, true fill) {np.corrcoef(g['fill_pct'], g['true_fill_pct'])[0, 1]:+.2f}")
    curves = pd.read_parquet(path)
    first_bin = curves["bin_id"].iloc[0]
    c = curves[curves["bin_id"] == first_bin]
    hb = h[h["bin_id"] == first_bin].set_index("ts")["true_fill_pct"]
    keys = c.drop_duplicates(["ts", "stroke_in_hour"])[["ts", "stroke_in_hour"]]
    keys = keys.iloc[np.linspace(0, len(keys) - 1, min(120, len(keys))).astype(int)]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    cmap = plt.get_cmap("viridis")
    for ts, k in keys.itertuples(index=False):
        one = c[(c["ts"] == ts) & (c["stroke_in_hour"] == k)]
        fill = hb.get(ts.floor("h"), np.nan)
        ax.plot(one["ram_position"], one["pressure_bar"], color=cmap(np.nan_to_num(fill) / 100), lw=0.8, alpha=0.7)
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(0, 100))
    fig.colorbar(sm, ax=ax, label="true fill (%)")
    ax.set(xlabel="ram position (0 = start, 1 = end of stroke)", ylabel="pressure (bar)",
           title=f"Pressure graph per stroke ({first_bin}): contact point moves forward as the container fills")
    fig.tight_layout()
    fig.savefig(plots / "pressure_graph.png", dpi=110)
    plt.close(fig)


def check_false_full(d) -> None:
    lifts = d["lifts"]
    ff = lifts["false_full_trigger"]
    print("\n== False-full readings ==")
    print(f"share of lifts triggered by false-full readings: {ff.mean():.1%}")
    print(f"mean true fill before lift: false-full {lifts.loc[ff, 'true_fill_before_pct'].mean():.0f} % vs "
          f"other {lifts.loc[~ff, 'true_fill_before_pct'].mean():.0f} %; mean mass "
          f"{lifts.loc[ff, 'true_mass_removed_kg'].mean():.0f} vs {lifts.loc[~ff, 'true_mass_removed_kg'].mean():.0f} kg")


def check_integrity(d) -> None:
    h, s = d["hourly"], d["summary"]
    print("\n== Integrity ==")
    print(f"rows {len(h):,}; mass-balance max |residual| {s['mass_balance_max_abs_residual_kg']:.2e} kg "
          f"{status(s['mass_balance_max_abs_residual_kg'] < 1e-3)}")
    print(f"true fill range {h['true_fill_pct'].min():.1f}-{h['true_fill_pct'].max():.1f} %; "
          f"min true mass {h['true_mass_kg'].min():.1f} kg; fill NaN share {h['fill_pct'].isna().mean():.1%}")
    hours = d["lifts"]["ts"].dt.hour
    dow = d["lifts"]["ts"].dt.day_of_week
    print(f"lift hours {hours.min()}-{hours.max()}, Sunday lifts {(dow == 6).sum()}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run", type=Path)
    args = ap.parse_args(argv)
    plots = args.run / "plots"
    plots.mkdir(exist_ok=True)
    d = load_run(args.run)
    start = d["summary"]["start"]
    print(f"Run {args.run}: {d['summary']['n_bins']} bins, {d['summary']['days']} days, {len(d['lifts'])} lifts")
    check_integrity(d)
    check_lifts(d, plots)
    check_weather(d, plots, start)
    check_weather_effect(d, plots)
    check_pressure(d, plots, np.random.default_rng(0))
    check_pressure_graph(args.run, d, plots)
    check_false_full(d)
    print(f"\nplots written to {plots}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
