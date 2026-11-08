"""What a real device knows. Everything else is truth and must never become a model input."""
from __future__ import annotations

from typing import Iterable

# Hourly columns a real compactor + its SMART service + public weather data can provide.
DEVICE_COLUMNS = (
    "bin_id", "ts", "material", "equipment_class", "container_volume_m3", "region", "infeed", "drainage",
    "temp_c", "precip_mm_day", "rh_pct", "fill_pct", "presses", "peak_pressure_bar", "lift_event",
)
CURVE_COLUMNS = ("curve_contact_pos", "curve_rise_slope_bar", "curve_mean_bar", "curve_completion")
OPTIONAL_DEVICE_COLUMNS = ("energy_kwh", "precip_mm") + CURVE_COLUMNS   # pressure graph
# The hydraulic oil temperature is deliberately NOT used (no oil sensor assumed on real machines);
# the pressure inversion uses the air temperature as its viscosity proxy instead.
REQUIRED_PREDICT_COLUMNS = DEVICE_COLUMNS

# Simulator outputs that are truth even though they do not start with true_.
FORBIDDEN_COLUMNS = frozenset({
    "lifted_mass_kg", "side_waste_kg", "ref_scale_kg",
    # hidden machine truth from bins.parquet
    "density_factor", "daily_input_kg", "target_cycle_days", "p_idle_bar", "k_machine", "relief_bar",
    "fill_gain_bias", "pressure_gain_bias", "pressure_gain_drift_per_year", "storage_outdoor_share",
    "threshold_pct",
})
FORBIDDEN_PREFIXES = ("true_",)


class LeakageError(ValueError):
    pass


def leaking_columns(columns: Iterable[str]) -> list[str]:
    return [c for c in columns if c.startswith(FORBIDDEN_PREFIXES) or c in FORBIDDEN_COLUMNS]


def assert_no_leakage(columns: Iterable[str]) -> None:
    bad = leaking_columns(columns)
    if bad:
        raise LeakageError(f"truth columns in the feature matrix: {bad}")
