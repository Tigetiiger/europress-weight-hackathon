"""Section 4: one material's error at lift versus the number of training machines (machines-needed curve)."""
from __future__ import annotations

import shutil
from html import escape
from pathlib import Path

from site_common import IMG, ROOT, SITE, Build, figures, h2, h3, load_json, note, num, p, pct_of, register_source, table

TARGET = 5.0


def _interpretation(res: dict) -> str:
    pts = res["points"]
    unseen = [(pt["n_machines"], pt["unseen"]["mean_pct"]) for pt in pts]
    best_n, best = min(unseen, key=lambda t: t[1])
    first_ok = next((n for n, v in unseen if v < TARGET), None)
    near = next(n for n, v in unseen if v <= best + 0.5)
    parts = [f"With a single training machine the {res['material']} model is off by {num(unseen[0][1])} % on unseen "
             f"machines (naive fill × density: {num(res.get('naive_unseen_mean_pct'))} %).",
             f"The best value is {num(best)} % (at {best_n} machines); the curve is within 0.5 pp of it from {near} machines on."]
    parts.append(f"The 5 % target on unseen machines is first met at {first_ok} machines." if first_ok
                 else "The 5 % target on unseen machines is not reached with the training machines available.")
    parts.append(f"On the machines it was trained on (later months) the error is {num(pts[0]['same']['mean_pct'])} % with "
                 f"{pts[0]['n_machines']} machine(s): a model learns its own installation quickly.")
    return " ".join(parts)


def _copy_png(b: Build, model_run: Path, material: str) -> list[Path]:
    rel = model_run / "plots" / f"material_curve_{material}.png"
    src = b.check(rel)
    if src is None:
        return []
    dest = SITE / IMG / "model" / rel.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    register_source(IMG / "model" / rel.name, src)
    return [IMG / "model" / rel.name]


def _one_material(b: Build, model_run: Path, res: dict) -> str:
    mat = res["material"]
    ref = res.get("reference_model", {})
    params = ", ".join(f"{k} {v}" for k, v in (ref.get("params") or {}).items())
    out = [h3(f"{mat.replace('_', ' ')}", f"curve-{mat}"),
           p(f"A {escape(mat)}-only model is trained on 1, 2, 4, 8, … of the {res['train_pool_machines']} {escape(mat)} "
             f"training machines, using the tuned per-material settings of the 400-machine model ({escape(params)}, "
             f"{ref.get('best_iter')} trees, {res.get('n_features')} features including the pressure graph). Everything, "
             f"including the pressure inversion, is fitted on those machines only. <strong>Unseen</strong>: always the same "
             f"{res['test_machines']} held-out machines ({res['test_lifts_unseen']} lifts from {escape(res['test_start'][:10])}). "
             f"<strong>Same</strong>: the training machines themselves in the last 25 % of the year. Small sizes are repeated "
             "with different machine draws; the band in the figure and the range column show how much the result depends "
             "on which machines happen to be picked."),
           p(escape(_interpretation(res))), figures(_copy_png(b, model_run, mat), single=True)]
    rows = [[str(pt["n_machines"]), str(pt["repeats"]), num(pt["unseen"]["mean_pct"]),
             f"{num(pt['unseen']['min_pct'])}–{num(pt['unseen']['max_pct'])}", pct_of(pt["unseen"]["within_5pct"], 0),
             num(pt["same"]["mean_pct"]), pct_of(pt["same"]["within_5pct"], 0)] for pt in res["points"]]
    out.append(table(["training machines", "repeats", "unseen: mean % error", "unseen: range over repeats", "unseen: within 5 % (%)",
                      "same: mean % error", "same: within 5 % (%)"], rows, good_cols={2, 5},
                     caption=f"{mat.replace('_', ' ')} model: error at lift versus number of training machines."))
    out.append(p(f"Mean % error at lift (pre-lift hour). Naive fill × density on the unseen set: "
                 f"{num(res.get('naive_unseen_mean_pct'))} %. Compute for the whole curve: {num(res.get('wall_s'), 0)} s."))
    return "".join(out)


def material_curve_section(b: Build, model_run: Path) -> str:
    files = sorted((ROOT / model_run).glob("material_curve_*.json"))
    head = '<section id="sec-curve">' + h2("Machines needed for one material", "curve")
    intro = p("How many instrumented machines does a material-specific model need before it generalises to installations "
              "it has not seen? Section 3.6 answers this for the shared model in coarse steps; here one material is trained "
              "alone with finer steps and repeated draws.")
    if not files:
        return head + intro + note("The machines-needed curve was not available at build time.") + "</section>"
    parts = [_one_material(b, model_run, res) for res in (load_json(b, model_run / f.name) for f in files) if res]
    return head + intro + "".join(parts) + "</section>"
