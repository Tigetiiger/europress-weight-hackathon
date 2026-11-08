"""Weather -> water pathways for each load (state-independent, vectorised).

Pathways kept separate (each is a truth column):
  * humidity   : hygroscopic fibre moisture from the storage air (Oswin isotherm)
  * carried    : surface water picked up crossing a wet yard to an outdoor hopper
  * hopper     : rain/snow falling into the hopper, pressed in with the next load
                 (handled per hour; routing, drainage and frost live in container.py)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import material_vector
from .fleet import material_index
from .inflow import Inflow
from .timeline import Timeline
from .weather import Weather, ema


def emc_pct(rh_pct: np.ndarray, a: float, b: float, rh_max: float) -> np.ndarray:
    """Equilibrium moisture content (% dry basis), Oswin isotherm."""
    h = np.clip(rh_pct / 100.0, 0.01, rh_max)
    return a * (h / (1.0 - h)) ** b


@dataclass(frozen=True)
class LoadWater:
    w_hyg: np.ndarray            # (T, N) kg, hygroscopic water vs reference (may be < 0)
    w_carry_abs: np.ndarray      # (T, N) kg carried-in water held by the material
    w_carry_free: np.ndarray     # (T, N) kg carried-in water that runs off (plastics)
    hopper_rain_kg: np.ndarray   # (T, N) kg rain into the hopper
    hopper_snow_kg: np.ndarray   # (T, N) kg snow (water equivalent) into the hopper
    container_temp: np.ndarray   # (T, N) C, thermal mass of container + contents
    machine_ambient: np.ndarray  # (T, N) C, air around the hydraulic unit


def _per_bin(mapping: Mapping[str, float], keys: np.ndarray) -> np.ndarray:
    return np.array([float(mapping[k]) for k in keys])


def _hygroscopic_extra(mcfg, wx: Weather, fleet: pd.DataFrame, region_idx) -> np.ndarray:
    """(T, N) moisture extra (kg water per kg reference mass) from storage air."""
    tau = mcfg["storage_time_constant_h"]
    e = mcfg["emc_oswin"]
    emc_out = emc_pct(ema(wx.rh_h, tau), e["A"], e["B"], e["rh_max"])[:, region_idx]
    emc_in = emc_pct(ema(wx.rh_in_h, tau), e["A"], e["B"], e["rh_max"])[:, region_idx]
    share = fleet["storage_outdoor_share"].to_numpy()[None, :]
    mc = share * emc_out + (1.0 - share) * emc_in
    ref = mcfg["reference_mc_pct"]
    hyg = material_vector(mcfg["hygroscopic"])[material_index(fleet)][None, :]
    return hyg * (mc - ref) / (100.0 + ref)


def build_load_water(cfg: Mapping[str, Any], tl: Timeline, wx: Weather, fleet: pd.DataFrame,
                     inflow: Inflow) -> LoadWater:
    mcfg = cfg["moisture"]
    region_idx = np.array([wx.regions.index(r) for r in fleet["region"]])
    mat = material_index(fleet)
    infeed = fleet["infeed"].to_numpy()
    mass = inflow.mass_kg

    w_hyg = mass * _hygroscopic_extra(mcfg, wx, fleet, region_idx)
    carry = material_vector(mcfg["carry_in_frac"])[mat] * _per_bin(mcfg["outdoor_path"], infeed)
    w_carry = mass * carry[None, :] * wx.yard_wet[:, region_idx]
    absorbed = material_vector(mcfg["carry_absorbed_frac"])[mat][None, :]

    ingress = _per_bin(mcfg["hopper_ingress"], infeed) * fleet["hopper_area_m2"].to_numpy()
    indoor = fleet["indoor_installation"].to_numpy()[None, :]
    t_air = wx.temp_h[:, region_idx]
    t_cont = ema(wx.temp_h, mcfg["container_temp_time_constant_h"])[:, region_idx]
    t_indoor = mcfg["indoor_installation_temp_c"]
    return LoadWater(
        w_hyg=w_hyg,
        w_carry_abs=w_carry * absorbed,
        w_carry_free=w_carry * (1.0 - absorbed),
        hopper_rain_kg=wx.rain_h[:, region_idx] * ingress[None, :],   # 1 mm on 1 m2 = 1 kg
        hopper_snow_kg=wx.snow_h[:, region_idx] * ingress[None, :],
        container_temp=np.where(indoor, t_indoor, t_cont),
        machine_ambient=np.where(indoor, t_indoor, t_air),
    )


def load_reference_volume(cfg: Mapping[str, Any], tl: Timeline, fleet: pd.DataFrame, inflow: Inflow,
                          water: LoadWater, rng: np.random.Generator) -> np.ndarray:
    """(T, N) m3 each hour's load would occupy at the reference pack pressure.

    density = rho_typ * machine factor * daily factor * (1 + a * moisture_extra);
    absorbed water sits in the fibres, so it adds mass but not volume.
    """
    comp, mcfg = cfg["compaction"], cfg["moisture"]
    mat = material_index(fleet)
    rho = material_vector(comp["rho_typ_kg_m3"])[mat] * fleet["density_factor"].to_numpy()
    sigma = np.sqrt(np.log1p(comp["daily_factor_cv"] ** 2))
    daily = rng.lognormal(-0.5 * sigma**2, sigma, (tl.n_days, len(fleet)))[tl.day_idx]
    mass = inflow.mass_kg
    me = np.divide(water.w_hyg + water.w_carry_abs, mass, out=np.zeros_like(mass), where=mass > 0)
    wet = 1.0 + material_vector(mcfg["wet_density_coeff"])[mat][None, :] * np.clip(me, *mcfg["moisture_extra_clip"])
    return mass / (rho[None, :] * daily * wet)
