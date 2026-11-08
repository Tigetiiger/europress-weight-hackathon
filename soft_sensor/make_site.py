"""Build the technical documentation of the soft sensor into site/ (index.html, images, downloads).

Re-runnable: only includes inputs that exist at build time. Sections live in site_overview.py (1), site_model.py (2),
site_results.py (3), site_curve.py (4), site_sections.py (5), site_limits.py (6), site_extras.py (7 + footer) and
site_glossary.py (8 + tooltips); the figure gallery at the top and the figure descriptions in site_gallery.py; helpers in
site_common.py and site_style.py. Run make_hires_plots.py first to (re)create the hi-res twins opened by the image viewer.

    .venv/bin/python make_site.py [--model-run models/full400]
"""
from __future__ import annotations

import argparse
import logging
import re
import shutil
from datetime import datetime
from html import escape
from pathlib import Path

from site_common import FIGURES, IMG, ROOT, SITE, Build, clear_hires, copy_plots, dig, load_json, load_yaml, page
from site_curve import material_curve_section
from site_extras import downloads_section, footer, prepare_downloads
from site_gallery import FALLBACKS, configure, gallery
from site_glossary import build_glossary, glossary_section, wrap_terms
from site_limits import limitations
from site_model import feature_docs, model_inputs
from site_overview import overview
from site_results import pick, results
from site_sections import simulation_detail

SIM_RUN = Path("runs/full400")
PREFERRED_MODEL, FALLBACK_MODEL = Path("models/full400"), Path("models/bins25")
TITLE = "Hydraulic soft sensor for compactor waste mass — technical documentation"
LEGACY_DIRS = ("models", "runs", "generated")     # site folders written by earlier versions of this script


def newer(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_mtime > b.stat().st_mtime
    except OSError:
        return False


def default_model_run() -> Path:
    """The 400-machine model only if its metrics are newer than the simulator data it was trained on."""
    if newer(ROOT / PREFERRED_MODEL / "metrics.json", ROOT / SIM_RUN / "hourly.parquet"):
        return PREFERRED_MODEL
    logging.warning("%s metrics are missing or older than %s data; showing %s", PREFERRED_MODEL, SIM_RUN, FALLBACK_MODEL)
    return FALLBACK_MODEL


HOME_LINK = '<a href="/" style="display:inline-block;margin:0 0 1rem;font-size:.85rem;color:var(--muted);text-decoration:none">← SMART Weight home</a>'  # back to the landing page served at / by dashboard/server


def toc(body: str) -> str:
    """Sidebar table of contents from the numbered h2/h3 headings of the body."""
    items: list[tuple[int, str, str, str]] = []
    for m in re.finditer(r'<h([23]) id="([^"]+)"><span class="n">([\d.]+)</span>(.*?)</h\1>', body):
        items.append((int(m.group(1)), m.group(2), m.group(3), re.sub(r"<[^>]+>", "", m.group(4))))
    out, open_sub = ['<nav class="toc" aria-label="Contents">' + HOME_LINK + '<p>Contents</p><ol>'], False
    for level, anchor, n, title in items:
        if level == 2:
            if open_sub:
                out.append("</ol>")
                open_sub = False
            if len(out) > 1:
                out.append("</li>")
            out.append(f'<li><a href="#{anchor}"><span class="n">{n}</span>{title}</a>')
        else:
            if not open_sub:
                out.append("<ol>")
                open_sub = True
            out.append(f'<li><a href="#{anchor}"><span class="n">{n}</span>{title}</a></li>')
    if open_sub:
        out.append("</ol>")
    out.append("</li></ol></nav>")
    return "".join(out)


BUSINESS_LINK = ' Business side: <a href="business.html">business model and summary of the whole submission</a>.'  # companion page site/business.html (hand-written, not generated)


def head_block(metrics: dict | None, model_run: Path) -> str:
    return (f"<h1>{escape(TITLE)}</h1>"
            f'<p class="meta">Europress × Since AI Hackathon 2026 — model side. Model version: {escape(model_run.name)} '
            f"(trained on the {dig(metrics, 'compute', 'machines') or '–'}-machine simulation run, seed "
            f"{escape(str(dig(metrics, 'settings', 'seed') or '–'))}). Document built {escape(datetime.now().strftime('%Y-%m-%d'))}."
            f'{BUSINESS_LINK}</p>')


def build(model_run: Path | None = None) -> Build:
    b = Build()
    SITE.mkdir(exist_ok=True)
    model_run = model_run or default_model_run()
    metrics = load_json(b, model_run / "metrics.json")
    data_run = Path(dig(metrics, "data") or SIM_RUN)
    logging.info("model run: %s (data %s)", model_run, data_run)
    summary = load_json(b, SIM_RUN / "summary.json")
    cfg = load_yaml(b, SIM_RUN / "config_used.yaml") or load_yaml(b, Path("config.yaml"))
    b.check(Path("README.md"))
    docs = feature_docs()
    glossary, cats = build_glossary(cfg, docs)
    links = prepare_downloads(b, model_run, SIM_RUN)
    clear_hires()
    FIGURES.clear()
    configure(b, metrics, summary, cfg, model_run, SIM_RUN)
    pngs = {p.stem: p for p in copy_plots(b, model_run / "plots", IMG / "model")}
    sim_pngs = {p.stem: p for p in copy_plots(b, SIM_RUN / "plots", IMG / "sim")}
    sections = (overview(b, metrics, summary, cfg, pick(pngs, "lift_error_by_material"))
                + model_inputs(b, metrics, model_run, data_run, docs, pngs)
                + results(b, metrics, model_run, SIM_RUN, pngs)
                + material_curve_section(b, model_run)
                + simulation_detail(b, SIM_RUN, summary, cfg, sim_pngs, links.get("validation"))
                + limitations(b, metrics, summary, cfg, SIM_RUN)
                + downloads_section(b)
                + glossary_section(glossary, cats)
                + footer(b, model_run, SIM_RUN, metrics))
    body = head_block(metrics, model_run) + gallery(FIGURES) + sections     # gallery needs every figure: built last
    (SITE / "index.html").write_text(page(TITLE, wrap_terms(body, glossary), toc(body), glossary))
    for name in LEGACY_DIRS:
        if (SITE / name).is_dir():
            shutil.rmtree(SITE / name)
    return b


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model-run", type=Path, default=None,
                    help=f"trained model folder to document (default: {PREFERRED_MODEL} if its metrics are newer than the "
                         f"{SIM_RUN} data, else {FALLBACK_MODEL})")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run = args.model_run
    if run is not None and run.is_absolute():
        run = run.relative_to(ROOT)
    b = build(run)
    logging.info("wrote %s", SITE / "index.html")
    for m in dict.fromkeys(b.missing):
        logging.info("missing at build time: %s", m)
    logging.info("%d figures, %d with a hi-res twin", len(FIGURES), sum(f.href != f.src for f in FIGURES))
    for stem in FALLBACKS:
        logging.warning("figure %s has only a generic description", stem)


if __name__ == "__main__":
    main()
