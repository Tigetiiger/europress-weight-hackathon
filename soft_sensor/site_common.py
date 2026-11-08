"""Shared helpers for the documentation site: input bookkeeping, numbering, formatting, tables, figures, markdown."""
from __future__ import annotations

import json
import logging
import math
import re
import shutil
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from site_style import page  # noqa: F401  (re-exported for make_site)

if TYPE_CHECKING:
    from matplotlib.figure import Figure

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
IMG = Path("img")                 # site-relative image folders: img/model, img/sim, img/gen
DOWNLOADS = Path("downloads")
GOOD_PCT = 5.0
HIRES_TAG = "@hires"              # <stem>@hires.png: high-resolution twin, loaded only in the image viewer
HIRES_FACTOR = 2.5                # pixel density of the hi-res twin relative to the inline image
SPLIT_LABEL = {"bins": "unseen machines", "time": "same machines, later months"}
CAPTIONS = {
    "lifts": "Lifted mass per material (left) and days between lifts (right).",
    "temperature": "Monthly mean temperature per region (synthetic weather).",
    "weather_effect": "Cardboard lift mass in wet and humid versus dry weeks, by infeed type (machine-normalised).",
    "pressure_vs_fill": "Hourly peak ram pressure versus true fill, coloured by true pack density.",
    "pressure_graph": "Per-stroke pressure graphs of one machine, coloured by true fill: the contact point moves "
                      "earlier in the stroke as the container fills.",
    "pressure_examples": "Example pressure graphs at low, medium and high true fill (dot = detected contact point).",
    "fill_estimate": "Firmware fill estimate (fill_pct) versus true fill, all hourly readings of the run.",
    "ladder": "Comparison ladder: mean % error at lift after adding each signal family.",
    "learning_curve": "Mean % error at lift on unseen machines versus number of training machines.",
    "lift_error_by_material": "Mean % error at lift by material and split.",
    "coverage_by_material": "Coverage of the calibrated p10–p90 interval by material (target 80 %).",
    "pred_vs_true": "Predicted versus true mass in the hour before each lift",
    "mass_curves": "Hourly mass estimate with the p10–p90 interval over the test period, example machines",
    "feature_importance": "Feature importance (share of LightGBM split gain)",
    "shap": "SHAP summary of the pre-lift hours",
    "material_curve_cardboard": "Cardboard-only model: error at lift versus number of training machines (unseen and "
                                "same machines; band = range over repeated machine draws).",
}


@dataclass
class Numbering:
    sec: int = 0
    sub: int = 0
    fig: int = 0
    tab: int = 0

    def section(self) -> int:
        self.sec += 1
        self.sub = self.fig = self.tab = 0
        return self.sec

    def subsection(self) -> str:
        self.sub += 1
        return f"{self.sec}.{self.sub}"

    def figure(self) -> str:
        self.fig += 1
        return f"Figure {self.sec}.{self.fig}"

    def table(self) -> str:
        self.tab += 1
        return f"Table {self.sec}.{self.tab}"


NUM = Numbering()


@dataclass
class Build:
    """Tracks which inputs were used or missing, and facts computed by one section for use in another."""
    used: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    downloads: list[tuple[str, str, str, int]] = field(default_factory=list)   # (href, label, description, bytes)

    def check(self, rel: Path) -> Path | None:
        path = ROOT / rel
        (self.used if path.exists() else self.missing).append(str(rel))
        return path if path.exists() else None


# ---------------------------------------------------------------- headings
def h2(title: str, anchor: str) -> str:
    n = NUM.section()
    return f'<h2 id="{anchor}"><span class="n">{n}</span>{escape(title)}</h2>'


def h3(title: str, anchor: str) -> str:
    n = NUM.subsection()
    return f'<h3 id="{anchor}"><span class="n">{n}</span>{escape(title)}</h3>'


def h4(title: str) -> str:
    return f"<h4>{escape(title)}</h4>"


def section(anchor: str, title: str, *parts: str) -> str:
    return f'<section id="sec-{anchor}">' + h2(title, anchor) + "".join(parts) + "</section>"


# ---------------------------------------------------------------- formatting
def num(v: Any, nd: int = 1) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "–"
    return "–" if math.isnan(f) else f"{f:,.{nd}f}"


def sci(v: Any) -> str:
    return "–" if num(v) == "–" else f"{float(v):.1e}"


def pct_of(frac: Any, nd: int = 1) -> str:
    return "–" if num(frac) == "–" else num(float(frac) * 100, nd)


def dig(d: Any, *keys: str) -> Any:
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def note(text: str) -> str:
    return f'<p class="note">{escape(text)}</p>'


def p(text: str) -> str:
    """Paragraph with trusted inline HTML."""
    return f"<p>{text}</p>"


def code(text: str) -> str:
    return f"<code>{escape(text)}</code>"


def term(key: str, text: str | None = None) -> str:
    """Explicit tooltip span (used where automatic wrapping cannot pick the right glossary entry)."""
    return f'<span class="term" tabindex="0" data-term="{escape(key)}">{escape(text or key)}</span>'


# ---------------------------------------------------------------- tables
def tcap(caption: str | None) -> str:
    if caption is None:
        return ""
    return f'<p class="tcap"><b>{NUM.table()}.</b> {caption}</p>'


def table(header: list[str], rows: list[list[str]], good_cols: set[int] | None = None,
          caption: str | None = None, left_cols: set[int] | None = None) -> str:
    """Cells are plain strings (escaped here); numeric cells in good_cols below GOOD_PCT are highlighted."""
    left = left_cols or {0}
    head = "".join(f'<th{" class=\"l\"" if i in left else ""}>{escape(h)}</th>' for i, h in enumerate(header))
    body = []
    for row in rows:
        cells = []
        for i, c in enumerate(row):
            classes = [x for x in ("l" if i in left else "", "good" if good_cols and i in good_cols and _below(c, GOOD_PCT) else "") if x]
            cls = f' class="{" ".join(classes)}"' if classes else ""
            cells.append(f"<td{cls}>{escape(c)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return tcap(caption) + f'<div class="tbl"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def raw_table(header: list[str], rows: list[list[str]], left_cols: set[int] | None = None,
              row_class: list[str] | None = None, col_class: list[str] | None = None,
              caption: str | None = None) -> str:
    """Like table() but cells are trusted HTML (callers escape their own text)."""
    left = left_cols or {0}
    cc = col_class or [""] * len(header)

    def cls(i: int) -> str:
        names = " ".join(x for x in ("l" if i in left else "", cc[i] if i < len(cc) else "") if x)
        return f' class="{names}"' if names else ""
    head = "".join(f"<th{cls(i)}>{h}</th>" for i, h in enumerate(header))
    body = []
    for r, row in enumerate(rows):
        rc = f' class="{row_class[r]}"' if row_class and row_class[r] else ""
        body.append(f"<tr{rc}>" + "".join(f"<td{cls(i)}>{c}</td>" for i, c in enumerate(row)) + "</tr>")
    return tcap(caption) + f'<div class="tbl"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _below(cell: str, limit: float) -> bool:
    try:
        return float(cell.replace(",", "")) < limit
    except ValueError:
        return False


# ---------------------------------------------------------------- loading
def _read(b: Build, rel: Path, reader: Any) -> Any:
    path = b.check(rel)
    try:
        return None if path is None else reader(path)
    except (OSError, ValueError) as exc:  # JSONDecodeError is a ValueError
        logging.warning("cannot read %s: %s", rel, exc)
        return None


def load_json(b: Build, rel: Path) -> dict | None:
    return _read(b, rel, lambda p: json.loads(p.read_text()))


def load_csv(b: Build, rel: Path) -> pd.DataFrame | None:
    return _read(b, rel, pd.read_csv)


def load_yaml(b: Build, rel: Path) -> dict | None:
    import yaml

    def reader(path: Path) -> dict:
        try:
            return yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise ValueError(str(exc)) from exc
    return _read(b, rel, reader)


# ---------------------------------------------------------------- images
@dataclass(frozen=True)
class FigureEntry:
    """One numbered figure of the page, in document order (read by the figure gallery)."""
    label: str           # "Figure 3.2"
    stem: str
    src: str             # site-relative inline image
    href: str            # site-relative image opened in the viewer (hi-res twin if present)
    caption: str
    description: str


FIGURES: list[FigureEntry] = []
_SOURCES: dict[str, Path] = {}    # site-relative image -> the source PNG it was copied from


def hires_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}{HIRES_TAG}{path.suffix}")


def is_hires(path: Path) -> bool:
    return path.stem.endswith(HIRES_TAG)


def register_source(site_rel: Path, src: Path) -> None:
    """Remember where a copied image came from, so its hi-res twin can be copied when the image is used."""
    _SOURCES[site_rel.as_posix()] = src


def clear_hires() -> None:
    """Remove hi-res twins of earlier builds; this build copies or writes only those of the figures it shows."""
    for old in (SITE / IMG).rglob(f"*{HIRES_TAG}.png") if (SITE / IMG).is_dir() else ():
        old.unlink()


def hires_href(site_rel: Path) -> str:
    """Site-relative hi-res twin of an inline image (copied from its source when needed), else the image itself."""
    target = hires_path(site_rel)
    src = _SOURCES.get(site_rel.as_posix())
    if src is not None:
        src_hi = hires_path(src)
        if not src_hi.exists():
            logging.info("no hi-res twin for %s (run make_hires_plots.py)", src)
            return site_rel.as_posix()
        if src_hi.stat().st_mtime < src.stat().st_mtime:
            logging.warning("hi-res twin %s is older than %s; not used (re-run make_hires_plots.py)", src_hi, src)
            return site_rel.as_posix()
        shutil.copy2(src_hi, SITE / target)
    return target.as_posix() if (SITE / target).exists() else site_rel.as_posix()


def save_figure(fig: Figure, site_rel: Path, dpi: int) -> list[Path]:
    """Save a figure made by this site into site/<site_rel>, plus its hi-res twin; returns [site_rel]."""
    (SITE / site_rel).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(SITE / site_rel, dpi=dpi)
    fig.savefig(SITE / hires_path(site_rel), dpi=round(dpi * HIRES_FACTOR))
    return [site_rel]


def copy_plots(b: Build, rel_dir: Path, dest_rel: Path) -> list[Path]:
    """Copy rel_dir/*.png (not the hi-res twins) into site/<dest_rel>; return site-relative paths (sorted by name)."""
    src = b.check(rel_dir)
    if src is None:
        return []
    dest = SITE / dest_rel
    dest.mkdir(parents=True, exist_ok=True)
    pngs = sorted(png for png in src.glob("*.png") if not is_hires(png))
    for png in pngs:
        shutil.copy2(png, dest / png.name)
        register_source(dest_rel / png.name, png)
    return [dest_rel / png.name for png in pngs]


def caption(stem: str) -> str:
    base, _, split = stem.rpartition("_")
    if base in CAPTIONS and split in SPLIT_LABEL:
        return f"{CAPTIONS[base]} — {SPLIT_LABEL[split]}."
    return CAPTIONS.get(stem, stem.replace("_", " "))


def zoom_attrs(entry: FigureEntry) -> str:
    """Attributes of the click-to-enlarge link: the viewer opens href (hi-res) and falls back to data-lo."""
    return (f'class="zoom" href="{escape(entry.href)}" data-lo="{escape(entry.src)}" '
            f'data-cap="{escape(entry.label + ". " + entry.caption)}"')


def figures(paths: list[Path], single: bool = False) -> str:
    """Numbered figures with click-to-enlarge (hi-res twin in the viewer); caption from CAPTIONS, long text from
    site_gallery. Every figure is recorded in FIGURES for the gallery at the top of the page."""
    from site_gallery import describe   # late import: site_gallery uses the helpers of this module

    if not paths:
        return note("Plot not available at build time.")
    figs = []
    for path in paths:
        entry = FigureEntry(label=NUM.figure(), stem=path.stem, src=path.as_posix(), href=hires_href(path),
                            caption=caption(path.stem), description=describe(path.stem))
        FIGURES.append(entry)
        long = f' <span class="fdesc">{escape(entry.description)}</span>' if entry.description else ""
        figs.append(f'<figure id="fig-{escape(entry.stem)}"><a {zoom_attrs(entry)}><img src="{escape(entry.src)}" '
                    f'alt="{escape(entry.caption)}" loading="lazy"></a><figcaption><b>{entry.label}.</b> '
                    f"{escape(entry.caption)}{long}</figcaption></figure>")
    return f'<div class="figs{" one" if single else ""}">{"".join(figs)}</div>'


def pre(text: str) -> str:
    """A formula / code block (text is escaped)."""
    return f"<pre><code>{escape(text.strip(chr(10)))}</code></pre>"


# ---------------------------------------------------------------- tiny markdown (for the technical report page)
def md_inline(text: str) -> str:
    codes: list[str] = []

    def stash(m: re.Match) -> str:
        codes.append(f"<code>{m.group(1)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`([^`]+)`", stash, escape(text))
    s = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", r'<img src="\2" alt="\1" loading="lazy">', s)
    s = re.sub(r"\[([^\]]+)\]\(((?:https?://|#)[^)\s]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\[([^\]]+)\]\([^)\s]+\)", r"\1", s)  # local file links: keep text only
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], s)


def md_to_html(md: str) -> str:
    """Headings, fenced code, tables, flat lists, paragraphs, inline bold/em/code/links/images."""
    out: list[str] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre><code>" + escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
        elif line.startswith(">"):
            j = i
            while j < len(lines) and lines[j].startswith(">"):
                j += 1
            out.append("<blockquote>" + md_inline(" ".join(x.lstrip("> ") for x in lines[i:j])) + "</blockquote>")
            i = j
        elif m := re.match(r"(#{1,6})\s+(.*)", line):
            out.append(f"<h{len(m.group(1))}>{md_inline(m.group(2))}</h{len(m.group(1))}>")
            i += 1
        elif line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            i = _md_table(lines, i, out)
        elif re.match(r"\s*([*-]|\d+\.)\s+", line):
            i = _md_list(lines, i, out)
        elif not line.strip():
            i += 1
        else:
            j = i
            while j < len(lines) and lines[j].strip() and not re.match(r"(#|>|```|\s*\||\s*([*-]|\d+\.)\s)", lines[j]):
                j += 1
            j = max(j, i + 1)
            out.append("<p>" + md_inline(" ".join(lines[i:j])) + "</p>")
            i = j
    return "\n".join(out)


def _md_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _md_table(lines: list[str], i: int, out: list[str]) -> int:
    head = "".join(f"<th>{md_inline(c)}</th>" for c in _md_cells(lines[i]))
    j, rows = i + 2, []
    while j < len(lines) and lines[j].lstrip().startswith("|"):
        rows.append("<tr>" + "".join(f"<td>{md_inline(c)}</td>" for c in _md_cells(lines[j])) + "</tr>")
        j += 1
    out.append(f'<div class="tbl"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    return j


def _md_list(lines: list[str], i: int, out: list[str]) -> int:
    tag = "ol" if re.match(r"\s*\d+\.", lines[i]) else "ul"
    items: list[str] = []
    j = i
    while j < len(lines) and lines[j].strip():
        m = re.match(r"\s*(?:[*-]|\d+\.)\s+(.*)", lines[j])
        if m:
            items.append(m.group(1))
        elif items and lines[j].startswith((" ", "\t")):
            items[-1] += " " + lines[j].strip()
        else:
            break
        j += 1
    out.append(f"<{tag}>" + "".join(f"<li>{md_inline(t)}</li>" for t in items) + f"</{tag}>")
    return j
