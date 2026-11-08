"""Load, override and validate the simulator configuration (config.yaml)."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

MATERIALS = ("cardboard", "paper", "plastic_film", "mixed_waste")
FIBRE_MATERIALS = ("cardboard", "paper")
INFEEDS = ("open_hopper", "lidded_hopper", "indoor_shaft")
CLASSES = ("COMBI", "DUO")
SECTORS = ("retail", "warehouse", "industry", "hotel")
DAY_TYPES = ("weekday", "saturday", "sunday")


def load_config(
    path: str | Path | None = None,
    *,
    n_bins: int | None = None,
    days: int | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Read a YAML config, apply CLI overrides and validate it."""
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not cfg_path.is_file():
        raise FileNotFoundError(f"config file not found: {cfg_path}")
    with cfg_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    cfg = with_run_overrides(raw, n_bins=n_bins, days=days, seed=seed)
    validate_config(cfg)
    return cfg


def with_run_overrides(cfg: Mapping[str, Any], **overrides: Any) -> dict[str, Any]:
    """Return a deep copy of ``cfg`` with non-None ``run`` entries replaced."""
    out = copy.deepcopy(dict(cfg))
    for key, value in overrides.items():
        if value is not None:
            out["run"][key] = value
    return out


def material_vector(mapping: Mapping[str, float]) -> np.ndarray:
    """Per-material parameter as an array in MATERIALS order."""
    return np.array([float(mapping[m]) for m in MATERIALS])


def _check_shares(name: str, shares: Mapping[Any, float], errors: list[str]) -> None:
    total = float(sum(shares.values()))
    if abs(total - 1.0) > 1e-6:
        errors.append(f"{name} must sum to 1 (got {total:.4f})")
    if any(v < 0 for v in shares.values()):
        errors.append(f"{name} has negative shares")


def _check_fleet(cfg: Mapping[str, Any], errors: list[str]) -> None:
    fleet = cfg["fleet"]
    _check_shares("fleet.material_shares", fleet["material_shares"], errors)
    _check_shares("fleet.infeed_shares", fleet["infeed_shares"], errors)
    _check_shares("fleet.region_shares", fleet["region_shares"], errors)
    _check_shares("fleet.threshold_pct_shares", fleet["threshold_pct_shares"], errors)
    if set(fleet["material_shares"]) != set(MATERIALS):
        errors.append(f"fleet.material_shares keys must be {MATERIALS}")
    for material, rows in fleet["containers"].items():
        _check_shares(f"fleet.containers.{material}", {i: r[2] for i, r in enumerate(rows)}, errors)
        if any(r[0] not in CLASSES for r in rows):
            errors.append(f"fleet.containers.{material}: class must be one of {CLASSES}")
    for material, shares in fleet["sector_by_material"].items():
        _check_shares(f"fleet.sector_by_material.{material}", shares, errors)
    unknown_regions = set(fleet["region_shares"]) - set(cfg["weather"]["regions"])
    if unknown_regions:
        errors.append(f"regions without weather parameters: {sorted(unknown_regions)}")


def _check_sectors(cfg: Mapping[str, Any], errors: list[str]) -> None:
    for name in SECTORS:
        sec = cfg["sectors"].get(name)
        if sec is None:
            errors.append(f"sectors.{name} missing")
            continue
        if len(sec["hour_weights"]) != 24:
            errors.append(f"sectors.{name}.hour_weights needs 24 values")
        if len(sec["month_factor"]) != 12:
            errors.append(f"sectors.{name}.month_factor needs 12 values")
        if len(sec["day_factor"]) != 7:
            errors.append(f"sectors.{name}.day_factor needs 7 values")


def validate_config(cfg: Mapping[str, Any]) -> None:
    """Raise ValueError listing every problem found in the config."""
    errors: list[str] = []
    run = cfg["run"]
    if int(run["n_bins"]) < 1:
        errors.append("run.n_bins must be >= 1")
    if int(run["days"]) < 1:
        errors.append("run.days must be >= 1")
    _check_fleet(cfg, errors)
    _check_sectors(cfg, errors)
    mode = cfg["weather"]["mode"]
    if mode not in ("synthetic", "fmi_csv"):
        errors.append(f"weather.mode must be synthetic|fmi_csv (got {mode!r})")
    if mode == "fmi_csv" and not cfg["weather"]["fmi_csv"].get("path"):
        errors.append("weather.fmi_csv.path required when weather.mode == fmi_csv")
    lo, hi = cfg["operations"]["truck_hours"]
    if not 0 <= lo < hi <= 24:
        errors.append("operations.truck_hours must satisfy 0 <= start < end <= 24")
    if errors:
        raise ValueError("invalid config:\n  " + "\n  ".join(errors))
