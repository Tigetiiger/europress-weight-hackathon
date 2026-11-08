"""Stateful container model (the only part that runs hour by hour).

Each bin's pack is a stack of layers, one per loading day since the last lift.
A layer stores
    m_b     material mass at reference moisture (kg)
    w_hyg   hygroscopic water extra (kg, may be < 0 for dry indoor air)
    w_car   carried-in water held by the material (kg)
    w_hop   hopper rain / melt water absorbed by the material (kg)
    vref    volume it would occupy at the reference pack pressure (m3)
    vfrost  extra volume from frozen lumps (m3, thaws away when warm)
    pmax    highest pack pressure it has experienced (bar)
Relaxed layer volume = (vref + vfrost) / gain(pmax) with the log-law gain.
Right after a stroke the pack is compressed by the springback fraction and it
relaxes back over a few hours.  Free water (pool) and ice add mass, not volume.

Material that arrives in an hour without a stroke waits in the press chamber
and goes in with the next stroke.  Material that no longer fits is side waste.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .config import material_vector
from .fleet import material_index
from .physics import compaction_gain, pressure_demand, reference_gain

LAYER_FIELDS = ("m_b", "w_hyg", "w_car", "w_hop", "vref", "vfrost", "pmax")
CHAMBER_FIELDS = ("m_b", "w_hyg", "w_car", "vref", "water", "snow")
TOTAL_FIELDS = ("drained", "seeped", "side_waste", "lifted")


def safe_div(num: np.ndarray, den: np.ndarray, default: float = 0.0) -> np.ndarray:
    num = np.asarray(num, dtype=float)
    return np.divide(num, den, out=np.full_like(num, default), where=np.asarray(den) > 1e-12)


@dataclass(frozen=True)
class BinParams:
    """Per-bin constants (arrays of length N)."""

    volume: np.ndarray
    rho_typ: np.ndarray          # material typical density (for the pressure law)
    rho_machine: np.ndarray      # rho_typ * machine density factor
    c: np.ndarray                # compressibility
    s_ref: np.ndarray            # log-law gain at the reference pressure
    springback: np.ndarray
    retained: np.ndarray         # share of free water the material soaks up
    wet_coeff: np.ndarray
    frost_penalty: np.ndarray
    stiffness: np.ndarray
    wet_softening: np.ndarray
    k: np.ndarray                # pressure scale k_bar * k_machine
    p_idle: np.ndarray
    relief: np.ndarray
    drainage: np.ndarray
    seeps: np.ndarray            # no drainage and not waterproof
    is_duo: np.ndarray


@dataclass(frozen=True)
class Physics:
    """Scalar constants of the container model."""

    p0: float
    p_ref: float
    fill_onset: float
    fill_exponent: float
    density_exponent: float
    frost_stiffening: float
    freeze_c: float
    wet_me_full: float
    me_clip: tuple[float, float]
    thaw_decay: float
    melt_rate: float
    seep_decay: float
    relax_decay: float
    relax_after_press: float


def build_params(cfg: Mapping[str, Any], fleet: pd.DataFrame) -> tuple[BinParams, Physics]:
    comp, hyd, mcfg = cfg["compaction"], cfg["hydraulics"], cfg["moisture"]
    mat = material_index(fleet)

    def per(mapping: Mapping[str, float]) -> np.ndarray:
        return material_vector(mapping)[mat]

    c = per(comp["compressibility"])
    rho = per(comp["rho_typ_kg_m3"])
    drainage = fleet["drainage"].to_numpy(dtype=bool)
    tau_sb = comp["springback_time_constant_h"]
    par = BinParams(
        volume=fleet["container_volume_m3"].to_numpy(dtype=float),
        rho_typ=rho, rho_machine=rho * fleet["density_factor"].to_numpy(),
        c=c, s_ref=reference_gain(c, comp["p0_bar"], comp["p_ref_bar"]),
        springback=per(comp["springback"]), retained=per(mcfg["retained_frac"]),
        wet_coeff=per(mcfg["wet_density_coeff"]), frost_penalty=per(mcfg["frost"]["penalty"]),
        stiffness=per(hyd["stiffness"]), wet_softening=per(hyd["wet_softening"]),
        k=hyd["k_bar"] * fleet["k_machine"].to_numpy(),
        p_idle=fleet["p_idle_bar"].to_numpy(), relief=fleet["relief_bar"].to_numpy(),
        drainage=drainage, seeps=~drainage & ~fleet["waterproof"].to_numpy(dtype=bool),
        is_duo=fleet["equipment_class"].to_numpy() == "DUO",
    )
    phys = Physics(
        p0=comp["p0_bar"], p_ref=comp["p_ref_bar"], fill_onset=hyd["fill_onset"],
        fill_exponent=hyd["fill_exponent"], density_exponent=hyd["density_exponent"],
        frost_stiffening=hyd["frost_stiffening"], freeze_c=mcfg["frost"]["freeze_below_c"],
        wet_me_full=mcfg["frost"]["wet_me_full"], me_clip=tuple(mcfg["moisture_extra_clip"]),
        thaw_decay=math.exp(-1.0 / mcfg["frost"]["thaw_time_constant_h"]),
        melt_rate=mcfg["ice_melt_rate_per_c_h"],
        seep_decay=math.exp(-1.0 / mcfg["seepage_time_constant_h"]),
        relax_decay=math.exp(-1.0 / tau_sb),
        relax_after_press=1.0 - math.exp(-0.5 / tau_sb),  # strokes on average mid-hour
    )
    return par, phys


class ContainerFleet:
    """Vectorised state of all containers. Mutated in place for speed."""

    def __init__(self, par: BinParams, phys: Physics, n_layers: int):
        n = len(par.volume)
        self.par, self.phys, self.n_layers = par, phys, n_layers
        self.layers = {f: np.zeros((n, n_layers)) for f in LAYER_FIELDS}
        self.chamber = {f: np.zeros(n) for f in CHAMBER_FIELDS}
        self.cur = np.zeros(n, dtype=np.int64)
        self.layer_day = np.full(n, -1, dtype=np.int64)
        self.pool = np.zeros(n)
        self.ice = np.zeros(n)
        self.p_last = np.zeros(n)
        self.relax = np.ones(n)
        self.totals = {f: np.zeros(n) for f in TOTAL_FIELDS}

    # ------------------------------------------------------------------ queries
    def _gain_layers(self, idx) -> np.ndarray:
        par = self.par
        return compaction_gain(self.layers["pmax"][idx], par.c[idx, None], self.phys.p0, par.s_ref[idx, None])

    def relaxed_volume(self, idx=slice(None)) -> np.ndarray:
        lay = self.layers
        return ((lay["vref"][idx] + lay["vfrost"][idx]) / self._gain_layers(idx)).sum(axis=1)

    def _sums(self, idx=slice(None)) -> dict[str, np.ndarray]:
        return {f: self.layers[f][idx].sum(axis=1) for f in ("m_b", "w_hyg", "w_car", "w_hop", "vref", "vfrost")}

    def chamber_mass(self) -> np.ndarray:
        ch = self.chamber
        return ch["m_b"] + ch["w_hyg"] + ch["w_car"] + ch["water"] + ch["snow"]

    def total_mass(self) -> np.ndarray:
        s = self._sums()
        return s["m_b"] + s["w_hyg"] + s["w_car"] + s["w_hop"] + self.pool + self.ice + self.chamber_mass()

    def snapshot(self) -> dict[str, np.ndarray]:
        """Hour-end truth for all bins."""
        s = self._sums()
        relaxed = self.relaxed_volume()
        vol = relaxed * (1.0 - self.par.springback * (1.0 - self.relax))
        water = s["w_hyg"] + s["w_car"] + s["w_hop"]
        content = s["m_b"] + water + self.pool + self.ice
        return {
            "fill_pct": 100.0 * vol / self.par.volume,
            "mass_kg": content + self.chamber_mass(),
            "density": np.where(vol > 1e-3, safe_div(content, vol), np.nan),
            "moisture_extra": np.where(s["m_b"] > 1.0, safe_div(water + self.pool + self.ice, s["m_b"]), np.nan),
            "w_hyg": s["w_hyg"], "w_car": s["w_car"], "w_hop": s["w_hop"],
            "pool": self.pool.copy(), "ice": self.ice.copy(),
        }

    # ------------------------------------------------------------------ setup
    def initial_fill(self, mass: np.ndarray, day: int) -> None:
        """Pre-compacted material at reference moisture (start of burn-in)."""
        self.layers["m_b"][:, 0] = mass
        self.layers["vref"][:, 0] = mass / self.par.rho_machine
        self.layers["pmax"][:, 0] = self.phys.p_ref
        self.layer_day[:] = day

    # ------------------------------------------------------------------ hourly processes
    def environment(self, t_cont: np.ndarray) -> None:
        """Ice melt, seepage from standard containers, thawing of frozen lumps."""
        ph = self.phys
        warm = t_cont > ph.freeze_c
        melt = np.where(warm, self.ice * (1.0 - np.exp(-ph.melt_rate * (t_cont - ph.freeze_c))), 0.0)
        self.ice -= melt
        idx = np.flatnonzero(melt > 0)
        self._route_liquid(idx, melt[idx])
        seep = np.where(self.par.seeps, self.pool * (1.0 - ph.seep_decay), 0.0)
        self.pool -= seep
        self.totals["seeped"] += seep
        self.layers["vfrost"][warm] *= ph.thaw_decay

    def load_chamber(self, mass, w_hyg, w_carry_abs, w_free, vref, rain, snow) -> None:
        ch = self.chamber
        ch["m_b"] += mass
        ch["w_hyg"] += w_hyg
        ch["w_car"] += w_carry_abs
        ch["vref"] += vref
        ch["water"] += rain + w_free
        ch["snow"] += snow

    def press(self, pressed: np.ndarray, day: int, t_cont: np.ndarray, visc: np.ndarray):
        """Strokes this hour: push the chamber in, then compact. Returns (demand, side waste)."""
        n = len(pressed)
        demand, side = np.full(n, np.nan), np.zeros(n)
        idx = np.flatnonzero(pressed)
        if idx.size:
            side[idx] = self._insert_chamber(idx, day, t_cont[idx])
            self._flush_chamber_water(idx, t_cont[idx])
            demand[idx] = self._stroke(idx, visc[idx])
        return demand, side

    def relax_step(self, pressed: np.ndarray) -> None:
        ph = self.phys
        self.relax = np.where(pressed, ph.relax_after_press, 1.0 - (1.0 - self.relax) * ph.relax_decay)

    # ------------------------------------------------------------------ internals
    def _target_layer(self, idx: np.ndarray, day: int, has_new: np.ndarray) -> np.ndarray:
        cur = self.cur[idx]
        occupied = self.layers["m_b"][idx, cur] > 0
        advance = has_new & occupied & (self.layer_day[idx] != day)
        cur = np.where(advance, np.minimum(cur + 1, self.n_layers - 1), cur)
        self.cur[idx] = cur
        self.layer_day[idx] = np.where(has_new, day, self.layer_day[idx])
        return cur

    def _insert_chamber(self, idx: np.ndarray, day: int, t_cont: np.ndarray) -> np.ndarray:
        par, ph, ch, lay = self.par, self.phys, self.chamber, self.layers
        mb, vref = ch["m_b"][idx], ch["vref"][idx]
        wet_me = safe_div(ch["w_car"][idx], mb)
        frozen = t_cont < ph.freeze_c
        vfrost = vref * par.frost_penalty[idx] * np.clip(wet_me / ph.wet_me_full, 0.0, 1.0) * frozen
        # new material is pressed against a pack at >= the last stroke's pressure,
        # so this volume estimate is an upper bound and fill can never exceed 100 %
        gain_last = compaction_gain(self.p_last[idx], par.c[idx], ph.p0, par.s_ref[idx])
        v_chunk = (vref + vfrost) / gain_last
        avail = np.maximum(par.volume[idx] - self.relaxed_volume(idx), 0.0)
        accept = np.where(v_chunk > 0, np.minimum(1.0, safe_div(avail, v_chunk)), 0.0)
        side = (mb + ch["w_hyg"][idx] + ch["w_car"][idx]) * (1.0 - accept)
        j = self._target_layer(idx, day, accept * mb > 0)
        empty = lay["m_b"][idx, j] <= 0
        lay["pmax"][idx, j] = np.where(empty, self.p_last[idx], np.maximum(lay["pmax"][idx, j], self.p_last[idx]))
        for field, add in (("m_b", mb), ("w_hyg", ch["w_hyg"][idx]), ("w_car", ch["w_car"][idx]),
                           ("vref", vref), ("vfrost", vfrost)):
            lay[field][idx, j] += add * accept
        for field in ("m_b", "w_hyg", "w_car", "vref"):
            ch[field][idx] = 0.0
        self.totals["side_waste"][idx] += side
        return side

    def _flush_chamber_water(self, idx: np.ndarray, t_cont: np.ndarray) -> None:
        ch = self.chamber
        water = ch["water"][idx]
        frozen = t_cont < self.phys.freeze_c
        self.ice[idx] += ch["snow"][idx] + np.where(frozen, water, 0.0)
        self._route_liquid(idx[~frozen], water[~frozen])
        ch["water"][idx] = 0.0
        ch["snow"][idx] = 0.0

    def _route_liquid(self, idx: np.ndarray, water: np.ndarray) -> None:
        """Liquid water reaching the pack: part soaks in, the rest drains or pools."""
        if idx.size == 0:
            return
        j = self.cur[idx]
        has_material = self.layers["m_b"][idx, j] > 0
        absorbed = np.where(has_material, water * self.par.retained[idx], 0.0)
        self._absorb(idx, j, absorbed)
        free = water - absorbed
        drained = np.where(self.par.drainage[idx], free, 0.0)
        self.pool[idx] += free - drained
        self.totals["drained"][idx] += drained

    def _absorb(self, idx: np.ndarray, j: np.ndarray, absorbed: np.ndarray) -> None:
        """Wet fibre loses stiffness: the layer compacts denser (smaller vref)."""
        lay = self.layers
        lo, hi = self.phys.me_clip
        mb = lay["m_b"][idx, j]
        w_old = lay["w_hyg"][idx, j] + lay["w_car"][idx, j] + lay["w_hop"][idx, j]
        me_old = np.clip(safe_div(w_old, mb), lo, hi)
        me_new = np.clip(safe_div(w_old + absorbed, mb), lo, hi)
        a = self.par.wet_coeff[idx]
        lay["vref"][idx, j] *= (1.0 + a * me_old) / (1.0 + a * me_new)
        lay["w_hop"][idx, j] += absorbed

    def _stroke(self, idx: np.ndarray, visc: np.ndarray) -> np.ndarray:
        par, ph, lay = self.par, self.phys, self.layers
        vol = self.relaxed_volume(idx)
        s = self._sums(idx)
        water = s["w_hyg"] + s["w_car"] + s["w_hop"]
        demand = pressure_demand(
            fill=vol / par.volume[idx],
            density_ratio=safe_div(s["m_b"] + water, vol * par.rho_typ[idx], default=1.0),
            moisture_extra=safe_div(water, s["m_b"]),
            frost_share=safe_div(s["vfrost"], s["vref"]),
            par={"k": par.k[idx], "stiffness": par.stiffness[idx], "wet_softening": par.wet_softening[idx],
                 "density_exponent": ph.density_exponent, "frost_stiffening": ph.frost_stiffening,
                 "fill_onset": ph.fill_onset, "fill_exponent": ph.fill_exponent},
        )
        # the relief valve limits system pressure; cold oil eats part of it
        p_pack = np.clip(np.minimum(demand, par.relief[idx] / visc - par.p_idle[idx]), 0.0, None)
        lay["pmax"][idx] = np.maximum(lay["pmax"][idx], p_pack[:, None])
        self.p_last[idx] = p_pack
        return demand

    # ------------------------------------------------------------------ lift
    def lift(self, idx: np.ndarray, residue_frac: np.ndarray) -> dict[str, np.ndarray]:
        """Empty containers ``idx``; DUO keeps ``residue_frac`` of the layers."""
        lay, ch = self.layers, self.chamber
        keep = residue_frac
        s = self._sums(idx)
        out = {
            "base_kg": s["m_b"] * (1 - keep) + ch["m_b"][idx],
            "water_humidity_kg": s["w_hyg"] * (1 - keep) + ch["w_hyg"][idx],
            "water_carried_kg": s["w_car"] * (1 - keep) + ch["w_car"][idx],
            "water_hopper_kg": s["w_hop"] * (1 - keep),
            "free_water_kg": self.pool[idx] + ch["water"][idx],
            "ice_kg": self.ice[idx] + ch["snow"][idx],
        }
        out["mass_kg"] = sum(out.values())
        out["residue_kg"] = (s["m_b"] + s["w_hyg"] + s["w_car"] + s["w_hop"]) * keep
        pmax_top = np.where(lay["m_b"][idx] > 0, lay["pmax"][idx], 0.0).max(axis=1)
        for field in LAYER_FIELDS:
            lay[field][idx] = 0.0
        for field in ("m_b", "w_hyg", "w_car", "w_hop", "vref", "vfrost"):
            lay[field][idx, 0] = s[field] * keep
        lay["pmax"][idx, 0] = np.where(keep > 0, pmax_top, 0.0)
        for field in CHAMBER_FIELDS:
            ch[field][idx] = 0.0
        self.pool[idx] = 0.0
        self.ice[idx] = 0.0
        self.cur[idx] = 0
        self.layer_day[idx] = -1
        self.p_last[idx] = 0.0
        self.relax[idx] = 1.0
        self.totals["lifted"][idx] += out["mass_kg"]
        return out

    def ledger(self) -> dict[str, np.ndarray]:
        return {"mass_kg": self.total_mass(), **{k: v.copy() for k, v in self.totals.items()}}
