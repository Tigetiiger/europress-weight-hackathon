"""Cut a small demo slice out of a full simulator run.

The full ``runs/full400`` telemetry is ~215 MB, too large to ship. The dashboard's live
weight tab only needs the replay window (test period) plus a lookback for features, so
this script keeps:

* ``PER_MATERIAL`` machines per material (always including ``ALWAYS``),
* rows from ``test_start - LOOKBACK_DAYS`` to the end of the run,
* every region's daily weather (small).

Usage (after regenerating the full run, see README):

    python tools/make_demo_slice.py --src runs/full400 --dst runs/demo40 \
        --metrics models/full400/metrics.json
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd

PER_MATERIAL = 10
ALWAYS = ("BIN-0001",)          # default machine opened by the dashboard
LOOKBACK_DAYS = 40              # matches softsensor Settings.lookback_days
TIME_SERIES = ("hourly", "strokes", "lifts", "pressure_curves")
COPY_AS_IS = ("weather_daily.parquet", "summary.json", "config_used.yaml")


def pick_machines(bins: pd.DataFrame, per_material: int, always: tuple[str, ...]) -> list[str]:
    chosen = [b for b in always if b in set(bins["bin_id"])]
    for _, group in bins.sort_values("bin_id").groupby("material"):
        have = sum(b in chosen for b in group["bin_id"])
        extra = [b for b in group["bin_id"] if b not in chosen][: max(per_material - have, 0)]
        chosen.extend(extra)
    return sorted(chosen)


def read_since(metrics_path: Path, split: str) -> pd.Timestamp:
    metrics = json.loads(metrics_path.read_text())
    start = pd.Timestamp(metrics["splits"][split]["test_start"])
    return start - pd.Timedelta(days=LOOKBACK_DAYS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--dst", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--split", default="time")
    parser.add_argument("--per-material", type=int, default=PER_MATERIAL)
    args = parser.parse_args()

    args.dst.mkdir(parents=True, exist_ok=True)
    bins = pd.read_parquet(args.src / "bins.parquet")
    keep = pick_machines(bins, args.per_material, ALWAYS)
    since = read_since(args.metrics, args.split)

    bins[bins["bin_id"].isin(keep)].to_parquet(args.dst / "bins.parquet", index=False)
    for name in TIME_SERIES:
        path = args.src / f"{name}.parquet"
        if not path.is_file():
            continue
        frame = pd.read_parquet(path, filters=[("bin_id", "in", keep)])
        frame = frame[frame["ts"] >= since]
        frame.to_parquet(args.dst / f"{name}.parquet", index=False, compression="zstd")
        print(f"{name}: {len(frame):,} rows")
    for name in COPY_AS_IS:
        if (args.src / name).is_file():
            shutil.copy2(args.src / name, args.dst / name)
    (args.dst / "SLICE.json").write_text(json.dumps(
        {"source": str(args.src), "machines": keep, "since": str(since)}, indent=2))
    print(f"{len(keep)} machines since {since} -> {args.dst}")


if __name__ == "__main__":
    main()
