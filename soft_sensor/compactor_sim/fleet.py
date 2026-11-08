"""Sample the compactor fleet: machines, containers, sites and hidden truth."""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import MATERIALS, SECTORS


def lognormal_unit(cv: float, size: int, rng: np.random.Generator) -> np.ndarray:
    """Lognormal multipliers with mean 1 and coefficient of variation ``cv``."""
    sigma = np.sqrt(np.log1p(cv**2))
    return rng.lognormal(-0.5 * sigma**2, sigma, size)


def allocate_exact(shares: Mapping[str, float], n: int, rng: np.random.Generator) -> np.ndarray:
    """Exact counts by largest remainder (so 25 bins still get every material), shuffled."""
    keys = list(shares)
    p = np.array([shares[k] for k in keys], dtype=float)
    raw = p / p.sum() * n
    counts = np.floor(raw).astype(int)
    order = np.argsort(-(raw - counts), kind="stable")
    counts[order[: n - counts.sum()]] += 1
    labels = np.repeat(np.array(keys, dtype=object), counts)
    return rng.permutation(labels)


def draw(mapping: Mapping[Any, float], size: int, rng: np.random.Generator) -> np.ndarray:
    keys = list(mapping)
    p = np.array([mapping[k] for k in keys], dtype=float)
    return np.array(keys, dtype=object)[rng.choice(len(keys), size=size, p=p / p.sum())]


def _containers(fc: Mapping[str, Any], material: np.ndarray, rng: np.random.Generator):
    cls = np.empty(len(material), dtype=object)
    vol = np.zeros(len(material))
    for mat in MATERIALS:
        idx = np.flatnonzero(material == mat)
        rows = fc["containers"][mat]
        p = np.array([r[2] for r in rows], dtype=float)
        pick = rng.choice(len(rows), size=len(idx), p=p / p.sum())
        cls[idx] = [rows[i][0] for i in pick]
        vol[idx] = [float(rows[i][1]) for i in pick]
    return cls, vol


def _tare(fc: Mapping[str, Any], cls: np.ndarray, vol: np.ndarray) -> np.ndarray:
    tare = np.zeros(len(cls))
    for name, (lo, hi) in fc["tare_kg"].items():
        idx = cls == name
        vols = [float(r[1]) for rows in fc["containers"].values() for r in rows if r[0] == name]
        vmin, vmax = min(vols), max(vols)
        frac = (vol[idx] - vmin) / max(vmax - vmin, 1e-9)
        tare[idx] = lo + (hi - lo) * frac
    return tare


def _machine(fc: Mapping[str, Any], cls: np.ndarray, rng: np.random.Generator) -> pd.DataFrame:
    model = np.empty(len(cls), dtype=object)
    for name, shares in fc["press_model_shares"].items():
        idx = np.flatnonzero(cls == name)
        model[idx] = draw(shares, len(idx), rng)
    spec = fc["press_models"]
    width = np.array([spec[m]["aperture_m"][0] for m in model], dtype=float)
    height = np.array([spec[m]["aperture_m"][1] for m in model], dtype=float)
    return pd.DataFrame({
        "press_model": model.astype(str),
        "force_t": [float(spec[m]["force_t"]) for m in model],
        "cycle_s": [float(spec[m]["cycle_s"]) for m in model],
        "motor_kw": [float(spec[m]["motor_kw"]) for m in model],
        "aperture_w_m": width,
        "aperture_h_m": height,
        "hopper_area_m2": width * height,
    })


def _sectors(fc: Mapping[str, Any], material: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    sector = np.empty(len(material), dtype=object)
    for mat in MATERIALS:
        idx = np.flatnonzero(material == mat)
        sector[idx] = draw(fc["sector_by_material"][mat], len(idx), rng)
    return sector


def _storage_share(fc: Mapping[str, Any], infeed: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    share = np.zeros(len(infeed))
    for name, (a, b) in fc["storage_outdoor_beta"].items():
        idx = np.flatnonzero(infeed == name)
        share[idx] = rng.beta(a, b, len(idx))
    return share


def _options(fc: Mapping[str, Any], df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(df)
    drainage = rng.random(n) < fc["drainage_share"]
    waterproof = ~drainage & (rng.random(n) < fc["waterproof_share_of_undrained"])
    indoor = (df["infeed"].to_numpy() == "indoor_shaft") & (
        rng.random(n) < fc["indoor_installation_share_of_shaft"])
    fr = fc["floating_roof"]
    floating = ((df["material"].to_numpy() == fr["material"])
                & (df["container_volume_m3"].to_numpy() >= fr["min_volume_m3"])
                & (rng.random(n) < fr["share"]))
    return df.assign(drainage=drainage, waterproof=waterproof,
                     indoor_installation=indoor, floating_roof=floating)


def _hidden_truth(cfg: Mapping[str, Any], df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Per-machine latent parameters the ML model must not see directly."""
    n = len(df)
    comp, hyd, sen = cfg["compaction"], cfg["hydraulics"], cfg["sensors"]
    force_factor = (df["force_t"].to_numpy() / comp["force_ref_t"]) ** comp["force_exponent"]
    roof = 1.0 + cfg["fleet"]["floating_roof"]["density_gain"] * df["floating_roof"].to_numpy()
    density_factor = lognormal_unit(comp["machine_factor_cv"], n, rng) * force_factor * roof
    tc = cfg["fleet"]["target_cycle_days"]
    cycle = np.clip(tc["median"] * np.exp(tc["sigma_log"] * rng.standard_normal(n)), tc["min"], tc["max"])
    rho = np.array([comp["rho_typ_kg_m3"][m] for m in df["material"]])
    threshold_mass = df["container_volume_m3"].to_numpy() * rho * density_factor * df["threshold_pct"] / 100
    fs, ps = sen["fill"], sen["pressure"]
    return df.assign(
        density_factor=density_factor,
        target_cycle_days=cycle,
        daily_input_kg=threshold_mass / cycle,
        p_idle_bar=rng.uniform(*hyd["p_idle_bar"], n),
        k_machine=lognormal_unit(hyd["k_machine_cv"], n, rng),
        relief_bar=hyd["relief_bar"] * (1 + hyd["relief_cv"] * rng.standard_normal(n)),
        fill_gain_bias=np.clip(fs["gain_bias_sd"] * rng.standard_normal(n), -fs["gain_bias_clip"], fs["gain_bias_clip"]),
        pressure_gain_bias=ps["gain_bias_sd"] * rng.standard_normal(n),
        pressure_gain_drift_per_year=ps["gain_drift_sd_per_year"] * rng.standard_normal(n),
    )


def build_fleet(cfg: Mapping[str, Any], rng: np.random.Generator) -> pd.DataFrame:
    """One row per compactor (each machine is its own site)."""
    fc = cfg["fleet"]
    n = int(cfg["run"]["n_bins"])
    material = allocate_exact(fc["material_shares"], n, rng)
    cls, vol = _containers(fc, material, rng)
    infeed = draw(fc["infeed_shares"], n, rng)
    df = pd.DataFrame({
        "bin_id": [f"BIN-{i + 1:04d}" for i in range(n)],
        "site_id": [f"SITE-{i + 1:04d}" for i in range(n)],
        "material": material.astype(str),
        "equipment_class": cls.astype(str),
        "container_volume_m3": vol,
        "container_tare_kg": _tare(fc, cls, vol),
        "region": draw(fc["region_shares"], n, rng).astype(str),
        "sector": _sectors(fc, material, rng).astype(str),
        "infeed": infeed.astype(str),
        "threshold_pct": draw(fc["threshold_pct_shares"], n, rng).astype(float),
        "storage_outdoor_share": _storage_share(fc, infeed, rng),
        "open_shift_h": rng.choice(np.array(fc["opening_shift_h"]), n).astype(int),
        "ref_scale": rng.random(n) < fc["ref_scale_share"],
    })
    df = pd.concat([df, _machine(fc, cls, rng)], axis=1)
    df = _options(fc, df, rng)
    return _hidden_truth(cfg, df, rng)


def sector_index(fleet: pd.DataFrame) -> np.ndarray:
    return np.array([SECTORS.index(s) for s in fleet["sector"]])


def material_index(fleet: pd.DataFrame) -> np.ndarray:
    return np.array([MATERIALS.index(m) for m in fleet["material"]])
