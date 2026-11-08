"""Section 7 (downloads) and the footer: copies selected files into site/downloads with friendly names."""
from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from html import escape
from pathlib import Path

import pandas as pd

from site_common import DOWNLOADS, IMG, ROOT, SITE, Build, h2, md_to_html, num, p, page

MAX_BYTES = 20 * 1024 * 1024
SAMPLE_MONTH = "2026-08"   # a full month inside the test period of both splits
TRUTH_COLS = ("true_", "lifted_mass_kg", "side_waste_kg", "ref_scale_kg")


def _size(n: int) -> str:
    return f"{n / 1024:.0f} kB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def _add(b: Build, name: str, label: str, desc: str) -> str | None:
    path = SITE / DOWNLOADS / name
    if not path.exists():
        return None
    if path.stat().st_size > MAX_BYTES:
        logging.info("download %s skipped: %s", name, _size(path.stat().st_size))
        path.unlink()
        return None
    href = (DOWNLOADS / name).as_posix()
    b.downloads.append((href, label, desc, path.stat().st_size))
    return href


def _copy(b: Build, src_rel: Path, name: str, label: str, desc: str) -> str | None:
    src = b.check(src_rel)
    if src is None:
        return None
    (SITE / DOWNLOADS).mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, SITE / DOWNLOADS / name)
    return _add(b, name, label, desc)


def _parquet_to_csv(b: Build, src_rel: Path, name: str, label: str, desc: str, **read_kw) -> str | None:
    src = b.check(src_rel)
    if src is None:
        return None
    try:
        df = pd.read_parquet(src, **read_kw)
        (SITE / DOWNLOADS).mkdir(parents=True, exist_ok=True)
        df.to_csv(SITE / DOWNLOADS / name, index=False)
    except (OSError, ValueError) as exc:
        logging.warning("cannot export %s: %s", src_rel, exc)
        return None
    return _add(b, name, label, desc)


def _sample(b: Build, sim_run: Path, model_run: Path) -> str | None:
    """Two machines x one month of hourly data, truth columns included, with the model's predictions joined."""
    bins_src, hourly_src = b.check(sim_run / "bins.parquet"), b.check(sim_run / "hourly.parquet")
    if bins_src is None or hourly_src is None:
        return None
    try:
        bins = pd.read_parquet(bins_src, columns=["bin_id", "material", "equipment_class"]).sort_values("bin_id")
        chosen = [bins[(bins["material"] == "cardboard") & (bins["equipment_class"] == "COMBI")]["bin_id"].iloc[0],
                  bins[(bins["material"] == "mixed_waste") & (bins["equipment_class"] == "DUO")]["bin_id"].iloc[0]]
        h = pd.read_parquet(hourly_src, filters=[("bin_id", "in", chosen)])
        h = h[h["ts"].dt.strftime("%Y-%m") == SAMPLE_MONTH]
        pred = ROOT / model_run / "predictions.parquet"
        if pred.exists():
            pr = pd.read_parquet(pred, filters=[("bin_id", "in", chosen)], columns=["bin_id", "ts", "mass_pred_kg", "mass_p10_kg", "mass_p90_kg"])
            h = h.merge(pr, on=["bin_id", "ts"], how="left")
        truth = [c for c in h.columns if c.startswith(TRUTH_COLS)]
        h = h[[c for c in h.columns if c not in truth] + truth]   # truth columns last
        (SITE / DOWNLOADS).mkdir(parents=True, exist_ok=True)
        name = "sample-hourly-2-machines-1-month.csv"
        h.to_csv(SITE / DOWNLOADS / name, index=False)
        b.facts["sample_desc"] = (f"{len(h):,} rows: machines {', '.join(chosen)} (one cardboard COMBI, one mixed-waste DUO), "
                                  f"{pd.Timestamp(SAMPLE_MONTH + '-01'):%B %Y}, inside the test period")
    except (OSError, ValueError, IndexError, KeyError) as exc:
        logging.warning("sample export failed: %s", exc)
        return None
    return _add(b, name, "Sample of hourly data with predictions (CSV)",
                f"{b.facts['sample_desc']}. Columns are those of Section 2.1 plus the model outputs mass_pred_kg, mass_p10_kg, "
                "mass_p90_kg. The last columns (true_… , lifted_mass_kg, side_waste_kg, ref_scale_kg) are simulator truth: "
                "they are included for checking and are never model inputs.")


def _report(b: Build, model_run: Path) -> str | None:
    src = b.check(model_run / "REPORT.md")
    if src is None:
        return None
    text = src.read_text()
    (SITE / DOWNLOADS).mkdir(parents=True, exist_ok=True)
    (SITE / DOWNLOADS / "technical-report.md").write_text(text)
    _add(b, "technical-report.md", "Technical report (Markdown)", "The report written by the training pipeline, as plain Markdown "
         "(image links refer to the plots folder of the model run).")
    html = md_to_html(re.sub(r"\]\(plots/([^)]+)\)", rf"](../{IMG.as_posix()}/model/\1)", text))
    body = f'<section><p><a href="../index.html#sec-results">← back to the documentation</a></p>{html}</section>'
    (SITE / DOWNLOADS / "technical-report.html").write_text(page("Technical report — hydraulic soft sensor", body))
    return _add(b, "technical-report.html", "Technical report (HTML)", "The same report rendered as a web page with its plots.")


def prepare_downloads(b: Build, model_run: Path, sim_run: Path) -> dict[str, str | None]:
    """Copy the downloadable files; returns name -> href for links from other sections."""
    out = {
        "metrics": _copy(b, model_run / "metrics.json", "evaluation-metrics.json", "Evaluation metrics (JSON)",
                         "Every number of Sections 1 and 3: per split, material and ladder step; breakdowns, learning curve, "
                         "feature importance, fitted pressure-law coefficients and training settings."),
        "ladder": _copy(b, model_run / "comparison.csv", "comparison-ladder.csv", "Comparison ladder (CSV)",
                        "Mean, median, within-5 %, within-10 %, MAE and bias at lift per split × ladder step × material."),
        "learning": _copy(b, model_run / "learning_curve.csv", "learning-curve.csv", "Learning curve (CSV)",
                          "Error at lift on unseen machines versus number of training machines, shared model."),
        "curve_csv": _copy(b, model_run / "material_curve_cardboard.csv", "machines-needed-cardboard.csv",
                           "Machines-needed curve, cardboard (CSV)", "Section 4 as a table: training machines, repeats, unseen "
                           "and same-machine error, range over repeats."),
        "curve_json": _copy(b, model_run / "material_curve_cardboard.json", "machines-needed-cardboard.json",
                            "Machines-needed curve, cardboard (JSON)", "The same with the reference model settings and timing."),
        "features": _copy(b, model_run / "feature_list.json", "feature-list.json", "Feature list (JSON)",
                          "Names of the features of each ladder feature set, as used by the trained models."),
        "config": _copy(b, sim_run / "config_used.yaml", "simulator-parameters.yaml", "Simulator parameters (YAML)",
                        "The complete configuration of the 400-machine run: every number of Section 5 with comments."),
        "summary": _copy(b, sim_run / "summary.json", "simulation-summary.json", "Simulation summary (JSON)",
                         "Counts, lift means per material and the mass-balance ledger of the run."),
        "validation": _copy(b, sim_run / "validate_report.txt", "validation-report.txt", "Simulator validation report (text)",
                            "The full report of the validation script (Section 5.9)."),
        "baseline": _copy(b, sim_run / "baseline_results.json", "early-baseline.json", "Early baseline results (JSON)",
                          "The 5-fold cross-validation baseline of Section 3.10, including per-segment errors."),
        "report": _report(b, model_run),
        "lifts": _parquet_to_csv(b, sim_run / "lifts.parquet", "lift-events.csv", "Lift events (CSV)",
                                 "One row per lift: order and lift time, trigger, false-full flag, observed and true fill, true "
                                 "removed mass split by water pathway, weighbridge net mass (with its simulated noise), tare and "
                                 "gross vehicle load. Truth columns are named as such."),
        "machines": _parquet_to_csv(b, sim_run / "bins.parquet", "machines.csv", "Machine and site attributes (CSV)",
                                    "One row per machine: configuration (material, class, volume, region, sector, infeed, "
                                    "drainage, press model) and the hidden truth (density_factor, p_idle_bar, k_machine, "
                                    "relief_bar, sensor biases, daily input, threshold) that the model never sees."),
        "weather": _parquet_to_csv(b, sim_run / "weather_daily.parquet", "weather-daily.csv", "Daily weather (CSV)",
                                   "Per region and day: mean/min/max temperature, precipitation, rain, snow, relative humidity "
                                   "and snowpack, including the 28 burn-in days."),
        "sample": _sample(b, sim_run, model_run),
    }
    return out


def downloads_section(b: Build) -> str:
    items = "".join(f'<li><a href="{escape(href)}" download>{escape(label)}</a><span class="sz">{_size(size)}</span>'
                    f'<span class="d">{escape(desc)}</span></li>' for href, label, desc, size in b.downloads)
    return ('<section id="sec-downloads">' + h2("Downloads", "downloads")
            + p("Files behind the tables and figures of this document. Everything is synthetic simulator output or derived "
                "from it. The large hourly and per-stroke tables (about 100 MB each) are not offered here; the sample below "
                "shows their format.")
            + f'<ul class="dl">{items}</ul></section>')


def footer(b: Build, model_run: Path, sim_run: Path, metrics: dict | None) -> str:
    def stamp(rel: Path) -> str:
        try:
            return datetime.fromtimestamp((ROOT / rel).stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            return "–"
    n = len(dict.fromkeys(b.used))
    missing = len(dict.fromkeys(b.missing))
    return (f"<footer><p>Hydraulic soft sensor for compactor waste mass — technical documentation. Built "
            f"{escape(datetime.now().strftime('%Y-%m-%d %H:%M'))} from {n} data files"
            + (f" ({missing} expected files were not available)" if missing else "") + ". "
            f"Model: LightGBM soft sensor, 400-machine simulation run (seed {escape(str((metrics or {}).get('settings', {}).get('seed', '–')))}), "
            f"evaluation metrics written {stamp(model_run / 'metrics.json')}; simulation data written {stamp(sim_run / 'hourly.parquet')}. "
            f"Europress × Since AI Hackathon 2026. All data synthetic; nothing here represents real Europress data or settings.</p></footer>")


__all__ = ["prepare_downloads", "downloads_section", "footer", "num"]
