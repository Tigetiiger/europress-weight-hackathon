"""Write a high-resolution twin <stem>@hires.png next to every plot the documentation site shows.

Re-runs the existing plot generators on the saved results — no simulation and no training — with matplotlib's
Figure.savefig patched so that each figure is written at HIRES_FACTOR x its normal dpi to <stem>@hires.png. The
normal-resolution PNGs are never touched. The site's own figures (site/img/gen) get their twins from make_site.py.

    .venv/bin/python make_hires_plots.py [--sim-run runs/full400] [--model-run models/full400]

Sources: (a) validate.py -> <sim-run>/plots (reads the run's parquet files, ~1 min; its text report goes to /dev/null),
(b) weight_train.plots.make_all_plots -> <model-run>/plots, (c) weight_train.material_curve.plot_material_curve for
every <model-run>/material_curve_<material>.json.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.figure  # noqa: E402

from site_common import HIRES_FACTOR, ROOT, hires_path  # noqa: E402

log = logging.getLogger("make_hires_plots")


@contextlib.contextmanager
def hires_savefig(written: list[Path], factor: float = HIRES_FACTOR) -> Iterator[None]:
    """Patch Figure.savefig: write <stem>@hires.<ext> at factor x the dpi the caller would have used."""
    original = matplotlib.figure.Figure.savefig

    def savefig(fig: matplotlib.figure.Figure, fname: object, *args: object, **kwargs: object) -> None:
        if not isinstance(fname, (str, os.PathLike)):
            raise TypeError("make_hires_plots only redirects saves to file paths")
        base = kwargs.pop("dpi", None)
        if base is None or base == "figure":
            rc = matplotlib.rcParams["savefig.dpi"]
            base = fig.dpi if rc == "figure" else rc
        target = hires_path(Path(fname))
        original(fig, target, *args, dpi=round(float(base) * factor), **kwargs)
        written.append(target)

    matplotlib.figure.Figure.savefig = savefig
    try:
        yield
    finally:
        matplotlib.figure.Figure.savefig = original


def validation_plots(sim_run: Path) -> None:
    import validate
    with contextlib.redirect_stdout(io.StringIO()):
        validate.main([str(sim_run)])


def model_plots(model_run: Path) -> None:
    from weight_train.plots import make_all_plots
    make_all_plots(model_run)


def material_curve_plots(model_run: Path) -> None:
    from weight_train.material_curve import plot_material_curve
    for path in sorted(model_run.glob("material_curve_*.json")):
        res = json.loads(path.read_text())
        plot_material_curve(res, model_run / "plots" / f"material_curve_{res['material']}.png")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sim-run", type=Path, default=Path("runs/full400"))
    ap.add_argument("--model-run", type=Path, default=Path("models/full400"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sim_run, model_run = ROOT / args.sim_run, ROOT / args.model_run
    jobs = [("validation plots", sim_run / "hourly.parquet", lambda: validation_plots(sim_run)),
            ("model plots", model_run / "metrics.json", lambda: model_plots(model_run)),
            ("material curve plots", model_run, lambda: material_curve_plots(model_run))]
    written: list[Path] = []
    failed = 0
    for name, needed, job in jobs:
        if not needed.exists():
            log.warning("skip %s: %s not found", name, needed)
            continue
        before = len(written)
        try:
            with hires_savefig(written):
                job()
        except Exception:  # one broken generator must not block the others
            log.exception("%s failed", name)
            failed += 1
            continue
        log.info("%s: %d hi-res files", name, len(written) - before)
    for path in written:
        log.info("wrote %s", path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
