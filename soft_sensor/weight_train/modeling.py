"""LightGBM fitting helpers. Target = kg per m3 of container (true_mass_kg / volume)."""
from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd

from .columns import assert_no_leakage
from .features import CATEGORICAL
from .settings import TrainSettings

MIN_LIFT_KG_FOR_PCT = 200.0   # % error is meaningless for near-empty (false-full) lifts


@dataclass
class FittedModel:
    booster: lgb.Booster
    features: list[str]
    best_iter: int
    params: dict
    monotone: bool
    objective: str

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.booster.predict(X[self.features], num_iteration=self.best_iter)


@dataclass(frozen=True)
class Rows:
    """Row indices + target + weights for one model fit."""

    idx: np.ndarray
    y: np.ndarray
    w: np.ndarray


def make_rows(mask: np.ndarray, pre_lift: np.ndarray, y: np.ndarray, s: TrainSettings,
              rng: np.random.Generator) -> Rows:
    """All pre-lift rows of ``mask`` + a random sample of the rest, capped."""
    lift_idx = np.flatnonzero(mask & pre_lift)
    other = np.flatnonzero(mask & ~pre_lift)
    room = max(s.max_rows_per_model - len(lift_idx), 0)
    if len(other) > room:
        other = np.sort(rng.choice(other, room, replace=False))
    idx = np.sort(np.concatenate([lift_idx, other]))
    w = np.where(pre_lift[idx], s.lift_weight, 1.0)
    return Rows(idx, y[idx], w)


def _dataset(X: pd.DataFrame, rows: Rows, features: list[str], reference=None) -> lgb.Dataset:
    cats = [c for c in features if c in CATEGORICAL]
    return lgb.Dataset(X.iloc[rows.idx][features], label=rows.y, weight=rows.w,
                       categorical_feature=cats, reference=reference, free_raw_data=True)


def fit(X: pd.DataFrame, train: Rows, val: Rows | None, features: list[str], s: TrainSettings,
        params: dict, objective: str = "regression", alpha: float | None = None,
        monotone: bool = False, rounds: int | None = None) -> FittedModel:
    assert_no_leakage(features)
    p = {**s.lgb_base(), **params, "objective": objective}
    if alpha is not None:
        p["alpha"] = alpha
    if monotone:
        p["monotone_constraints"] = [1 if f in s.monotone_features else 0 for f in features]
        p["monotone_constraints_method"] = "advanced"
    dtrain = _dataset(X, train, features)
    use_val = val is not None and len(val.idx) > 0 and rounds is None
    callbacks = []
    valid_sets = []
    if use_val:
        valid_sets = [_dataset(X, val, features, reference=dtrain)]
        callbacks = [lgb.early_stopping(s.early_stopping_rounds, verbose=False)]
    n_rounds = rounds if rounds is not None else (s.max_rounds if use_val else s.fixed_rounds)
    booster = lgb.train(p, dtrain, num_boost_round=n_rounds, valid_sets=valid_sets, callbacks=callbacks)
    best = booster.best_iteration if use_val and booster.best_iteration > 0 else n_rounds
    return FittedModel(booster, list(features), int(best), dict(params), monotone, objective)


def lift_pct_error(y_kg: np.ndarray, pred_kg: np.ndarray) -> float:
    ok = y_kg >= MIN_LIFT_KG_FOR_PCT
    if not ok.any():
        return float("nan")
    return float(np.mean(np.abs(pred_kg[ok] - y_kg[ok]) / y_kg[ok]) * 100)
