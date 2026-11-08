"""python -m weight_train {train,predict,report} ..."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .settings import TrainSettings


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="weight_train", description="Hydraulic soft sensor: hourly container mass.")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train", help="train + evaluate, write models/<run>")
    t.add_argument("--data", type=Path, required=True, help="simulator run folder (hourly.parquet, ...)")
    t.add_argument("--out", type=Path, required=True)
    t.add_argument("--split", choices=["bins", "time", "both"], default="both")
    t.add_argument("--setup", choices=["per_material", "shared", "both"], default="both")
    t.add_argument("--seed", type=int, default=7)
    t.add_argument("--no-report", action="store_true", help="skip plots and REPORT.md")
    pr = sub.add_parser("predict", help="predict hourly mass with p10/p90")
    pr.add_argument("--models", type=Path, required=True)
    pr.add_argument("--input", type=Path, required=True)
    pr.add_argument("--output", type=Path, required=True)
    pr.add_argument("--split", choices=["bins", "time"], default=None, help="bundle to use (default: time)")
    pr.add_argument("--strokes", type=Path, default=None, help="optional strokes.parquet")
    c = sub.add_parser("curve", help="one material: error vs number of training machines")
    c.add_argument("--data", type=Path, required=True)
    c.add_argument("--models", type=Path, required=True, help="trained folder (uses its bins bundle settings)")
    c.add_argument("--material", default="cardboard")
    c.add_argument("--sizes", default="1,2,3,4,6,8,10,12,16,24,32,48,64,96,128")
    r = sub.add_parser("report", help="(re)build plots and REPORT.md from a trained folder")
    r.add_argument("--models", type=Path, required=True)
    return p


def _report(models: Path) -> None:
    from .plots import make_all_plots
    from .report import write_report
    make_all_plots(models)
    write_report(models)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    args = _parser().parse_args(argv)
    try:
        if args.cmd == "train":
            from .train import run_train
            splits = ["bins", "time"] if args.split == "both" else [args.split]
            run_train(args.data, args.out, splits, args.setup, TrainSettings(seed=args.seed))
            if not args.no_report:
                _report(args.out)
        elif args.cmd == "predict":
            from .predict import run_predict
            run_predict(args.models, args.input, args.output, args.split, args.strokes)
        elif args.cmd == "curve":
            from .material_curve import run_material_curve
            sizes = tuple(int(x) for x in args.sizes.split(","))
            run_material_curve(args.data, args.models, args.material, sizes)
        else:
            _report(args.models)
    except (FileNotFoundError, ValueError) as exc:
        logging.error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
