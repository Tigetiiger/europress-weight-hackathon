"""Command line: python -m compactor_sim --config config.yaml --bins 400 --days 365 --seed 7 --out runs/x"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from .config import DEFAULT_CONFIG_PATH, load_config
from .output import write_run
from .simulate import run_simulation


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="compactor_sim", description="Synthetic hourly waste-compactor data.")
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="YAML config (default: ./config.yaml)")
    p.add_argument("--bins", type=int, default=None, help="number of compactors (overrides run.n_bins)")
    p.add_argument("--days", type=int, default=None, help="days to simulate (overrides run.days)")
    p.add_argument("--seed", type=int, default=None, help="random seed (overrides run.seed)")
    p.add_argument("--out", default=None, help="output folder (default runs/seed<seed>_<bins>x<days>)")
    p.add_argument("--no-strokes", action="store_true", help="skip strokes.parquet")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    args = parse_args(argv)
    try:
        cfg = load_config(args.config, n_bins=args.bins, days=args.days, seed=args.seed)
    except (FileNotFoundError, ValueError) as exc:
        logging.error("%s", exc)
        return 2
    if args.no_strokes:
        cfg["run"]["write_strokes"] = False
    run = cfg["run"]
    out = args.out or f"runs/seed{run['seed']}_{run['n_bins']}x{run['days']}"
    t0 = time.perf_counter()
    summary = write_run(run_simulation(cfg), out)
    logging.info("done in %.1f s -> %s", time.perf_counter() - t0, out)
    keys = ("n_bins", "hourly_rows", "n_lifts", "lift_mass_by_material",
            "false_full_lift_share", "mass_balance_max_abs_residual_kg")
    json.dump({k: summary[k] for k in keys}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0
