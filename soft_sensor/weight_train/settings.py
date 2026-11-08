"""Training settings and public nominal constants (nothing machine-specific or hidden)."""
from __future__ import annotations

from dataclasses import dataclass

MATERIALS = ("cardboard", "paper", "plastic_film", "mixed_waste")
CLASSES = ("COMBI", "DUO")
INFEEDS = ("open_hopper", "lidded_hopper", "indoor_shaft")

# Nominal compacted densities (kg/m3) from the product literature / task brief:
# public knowledge, not the per-machine hidden density factor.
NOMINAL_DENSITY = {"cardboard": 190.0, "paper": 330.0, "plastic_film": 140.0, "mixed_waste": 400.0}
# How much rain reaches the material per infeed type.
INFEED_EXPOSURE = {"open_hopper": 1.0, "lidded_hopper": 0.1, "indoor_shaft": 0.0}

# Nominal hydraulics used by the pressure inversion (a manufacturer knows these
# for its own product line; the per-machine values stay unknown).
P_IDLE_NOMINAL_BAR = 25.0
RELIEF_GUARD_BAR = 228.0          # ~95 % of a 240 bar relief valve: above it the reading saturates
VISC_COEFF_PER_C = 0.004
VISC_REF_C = 40.0
MIN_FILL_FOR_PRESSURE = 25.0      # below this the ram barely touches the pack


@dataclass(frozen=True)
class TrainSettings:
    seed: int = 7
    test_frac: float = 0.25            # share of time (and of machines for the bins split) held for test
    slice_frac: float = 0.20           # share of the training window / machines held for val + cal
    max_rows_per_model: int = 200_000  # random cap per model (all pre-lift rows are always kept)
    lift_weight: float = 5.0           # sample weight of pre-lift hours (the weighbridge comparison)
    learning_rate: float = 0.10
    max_rounds: int = 1500
    early_stopping_rounds: int = 50
    fixed_rounds: int = 400            # when a model has no validation rows
    grid: tuple = (
        {"num_leaves": 31, "min_data_in_leaf": 50},
        {"num_leaves": 31, "min_data_in_leaf": 200},
        {"num_leaves": 127, "min_data_in_leaf": 50},
        {"num_leaves": 127, "min_data_in_leaf": 200},
    )
    quantiles: tuple = (0.10, 0.90)
    target_coverage: float = 0.80
    fill_buckets: tuple = (0.0, 50.0, 80.0, 1000.0)   # Mondrian conformal strata (fill %)
    min_cal_points: int = 30
    learning_curve_sizes: tuple = (10, 25, 50, 100)
    shap_rows: int = 3000
    num_threads: int = 0                               # 0 = all cores
    monotone_features: tuple = ("fill_ffill", "presses_cycle")

    def lgb_base(self) -> dict:
        return {
            "learning_rate": self.learning_rate, "feature_fraction": 0.8, "bagging_fraction": 0.8,
            "bagging_freq": 1, "lambda_l2": 1.0, "seed": self.seed, "deterministic": True,
            "force_row_wise": True, "num_threads": self.num_threads, "verbose": -1,
        }
