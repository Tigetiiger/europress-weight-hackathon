"""Evaluation splits. Each has disjoint train / val (tuning, early stopping) / cal (conformal) / test rows.

bins : "new installation" — ~25 % of machines (stratified by material) are tested in the
       last 25 % of the period; models never see them. Val/cal come from other held-out
       machines in the training window, so calibration mimics unseen machines.
time : "pilot fleet" — all machines; train on the first 75 % of time minus a final slice
       that is split (alternating machines) into val and cal; test on the last 25 %.
Rows of the first, incomplete cycle of each machine are excluded everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .settings import TrainSettings

SPLIT_DESCRIPTIONS = {
    "bins": "unseen machines (25 % held out, stratified by material), tested on the last 25 % of the period",
    "time": "same machines, trained on the first 75 % of time and tested on the last 25 %",
}


@dataclass(frozen=True)
class Split:
    name: str
    train: np.ndarray
    val: np.ndarray
    cal: np.ndarray
    test: np.ndarray
    train_bins: tuple
    test_bins: tuple
    t_test: pd.Timestamp


def _bins_by_material(meta: pd.DataFrame) -> dict[str, list[str]]:
    first = meta.drop_duplicates("bin_id")
    return {m: sorted(g["bin_id"]) for m, g in first.groupby("material")}


def _stratified_pick(groups: dict[str, list[str]], frac: float, rng: np.random.Generator) -> set[str]:
    """frac of each material's machines, at least 1 and leaving at least 1 when n >= 2."""
    picked: set[str] = set()
    for _, bins in sorted(groups.items()):
        n = len(bins)
        k = 0 if n < 2 else int(np.clip(round(frac * n), 1, n - 1))
        picked.update(rng.permutation(bins)[:k])
    return picked


def _alternate(groups: dict[str, list[str]], subset: set[str]) -> tuple[set[str], set[str]]:
    a, b = set(), set()
    for _, bins in sorted(groups.items()):
        for i, bid in enumerate(sorted(x for x in bins if x in subset)):
            (a if i % 2 == 0 else b).add(bid)
    return a, b


def _bounds(ts: pd.Series, s: TrainSettings) -> tuple[pd.Timestamp, pd.Timestamp]:
    t0, t1 = ts.min(), ts.max()
    t_test = t0 + (1 - s.test_frac) * (t1 - t0)
    t_slice = t0 + (1 - s.test_frac) * (1 - s.slice_frac) * (t1 - t0)
    return t_slice, t_test


def make_split(meta: pd.DataFrame, kind: str, s: TrainSettings) -> Split:
    rng = np.random.default_rng(s.seed)
    ts, bins = meta["ts"], meta["bin_id"]
    complete = meta["cycle"].to_numpy() > 0
    t_slice, t_test = _bounds(ts, s)
    groups = _bins_by_material(meta)
    before_test = (ts < t_test).to_numpy()
    after_test = ~before_test
    if kind == "time":
        val_bins, cal_bins = _alternate(groups, set(bins.unique()))
        in_slice = ((ts >= t_slice) & (ts < t_test)).to_numpy()
        train = complete & (ts < t_slice).to_numpy()
        val = complete & in_slice & bins.isin(val_bins).to_numpy()
        cal = complete & in_slice & bins.isin(cal_bins).to_numpy()
        test = complete & after_test
        train_bins = test_bins = tuple(sorted(bins.unique()))
    elif kind == "bins":
        test_set = _stratified_pick(groups, s.test_frac, rng)
        rest = {m: [b for b in g if b not in test_set] for m, g in groups.items()}
        slice_set = _stratified_pick(rest, s.slice_frac, rng)
        val_bins, cal_bins = _alternate(rest, slice_set)
        train_set = {b for g in rest.values() for b in g} - slice_set
        train = complete & before_test & bins.isin(train_set).to_numpy()
        val = complete & before_test & bins.isin(val_bins).to_numpy()
        cal = complete & before_test & bins.isin(cal_bins).to_numpy()
        test = complete & after_test & bins.isin(test_set).to_numpy()
        train_bins, test_bins = tuple(sorted(train_set)), tuple(sorted(test_set))
    else:
        raise ValueError(f"unknown split {kind!r} (use bins|time)")
    return Split(kind, train, val, cal, test, train_bins, test_bins, t_test)
