"""Orchestration: vectorised pre-computation, then one loop over hours.

The loop is over time only; every operation inside is vectorised over bins, so
the cost is ~T iterations regardless of fleet size.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .container import ContainerFleet, build_params
from .fleet import build_fleet
from .inflow import Inflow, build_inflow
from .moisture import LoadWater, build_load_water, load_reference_volume
from .operations import TRIGGER_NAMES, Dispatcher
from .physics import oil_temperature, viscosity_factor
from .sensors import PressureFillEstimator, SensorPlan, build_sensor_plan, pressure_gain
from .strokes import StrokeResult, build_strokes
from .timeline import Timeline, build_timeline, next_allowed_index, truck_allowed
from .weather import Weather, build_weather

log = logging.getLogger(__name__)

RNG_STREAMS = ("fleet", "weather", "inflow", "density", "sensors", "operations", "strokes", "init", "lifts",
               "fill_est")
RECORD_FIELDS = ("fill_pct", "mass_kg", "density", "moisture_extra", "w_hyg", "w_car", "w_hop",
                 "pool", "ice", "fill_obs", "demand", "side_waste", "lifted")


@dataclass
class SimResult:
    cfg: Mapping[str, Any]
    timeline: Timeline
    fleet: pd.DataFrame
    weather: Weather
    inflow: Inflow
    water: LoadWater
    oil_end: np.ndarray
    dropout: np.ndarray
    records: dict[str, np.ndarray]      # (Tm, N) hour-end truth / observations
    lifts: pd.DataFrame
    strokes: StrokeResult
    ledger: dict[str, Any]
    timings: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class HourlyInputs:
    mass: np.ndarray
    w_hyg: np.ndarray
    w_carry_abs: np.ndarray
    w_carry_free: np.ndarray
    vref: np.ndarray
    rain: np.ndarray
    snow: np.ndarray
    presses: np.ndarray
    t_cont: np.ndarray
    visc: np.ndarray
    oil: np.ndarray


def _rngs(seed: int) -> dict[str, np.random.Generator]:
    children = np.random.SeedSequence(int(seed)).spawn(len(RNG_STREAMS))
    return {name: np.random.default_rng(s) for name, s in zip(RNG_STREAMS, children)}


class _Recorder:
    def __init__(self, n_hours: int, n_bins: int):
        self.data = {f: np.zeros((n_hours, n_bins), dtype=np.float32) for f in RECORD_FIELDS}
        self.data["ff_active"] = np.zeros((n_hours, n_bins), dtype=bool)
        self.data["lift_event"] = np.zeros((n_hours, n_bins), dtype=np.int8)

    def store(self, i: int, values: Mapping[str, np.ndarray]) -> None:
        for key, value in values.items():
            if key in self.data:
                self.data[key][i] = value


def _lift_rows(h, idx, out, disp, state, ocfg, rng) -> dict[str, np.ndarray]:
    wb = ocfg["weighbridge"]
    noisy = out["mass_kg"] + wb["noise_sd_kg"] * rng.standard_normal(idx.size)
    return {
        "bin_idx": idx, "lift_hour": np.full(idx.size, h), "order_hour": disp.order_hour[idx].copy(),
        "trigger": disp.trigger[idx].copy(), "false_full_trigger": disp.false_full[idx].copy(),
        "obs_fill_at_order_pct": disp.obs_fill[idx].copy(), "true_fill_at_order_pct": disp.true_fill[idx].copy(),
        "true_fill_before_pct": state["last_fill"][idx].copy(),
        "days_since_last_lift": state["age_h"][idx] / 24.0,
        "side_waste_since_last_kg": state["side_since"][idx].copy(),
        "weighbridge_net_kg": np.round(noisy / wb["resolution_kg"]) * wb["resolution_kg"],
        **out,
    }


def _do_lifts(h, box, disp, plan, state, ocfg, rngs, lift_rows, record) -> tuple[np.ndarray, np.ndarray]:
    """Lifts due at the start of hour h. Returns (lifted mass, lift flag) per bin."""
    lifted = np.zeros(len(box.par.volume))
    flag = np.zeros(len(box.par.volume), dtype=bool)
    idx = disp.due(h)
    if idx.size == 0:
        return lifted, flag
    lo, hi = ocfg["duo_residue_frac"]
    residue = np.where(box.par.is_duo[idx], rngs["lifts"].uniform(lo, hi, idx.size), 0.0)
    out = box.lift(idx, residue)
    if record:
        lift_rows.append(_lift_rows(h, idx, out, disp, state, ocfg, rngs["lifts"]))
    state["age_h"][idx] = 0.0
    state["side_since"][idx] = 0.0
    state["cancelled_ff"][idx] = plan.ff_id[h, idx]   # bridging clears when the container is emptied
    if state.get("fill_est") is not None:
        state["fill_est"].reset(idx)
    disp.complete(idx)
    lifted[idx] = out["mass_kg"]
    flag[idx] = True
    return lifted, flag


def _initial_state(cfg, fleet, box, rng) -> dict[str, np.ndarray]:
    lo, hi = cfg["operations"]["initial_fill_frac"]
    thr_mass = box.par.rho_machine * box.par.volume * fleet["threshold_pct"].to_numpy() / 100.0
    mass0 = rng.uniform(lo, hi, len(fleet)) * thr_mass
    box.initial_fill(mass0, day=0)
    n = len(fleet)
    return {
        "age_h": mass0 / fleet["daily_input_kg"].to_numpy() * 24.0,
        "last_fill": np.zeros(n),
        "side_since": np.zeros(n),
        "cancelled_ff": np.full(n, -1, dtype=np.int32),
    }


def run_loop(cfg, tl: Timeline, fleet: pd.DataFrame, inp: HourlyInputs, plan: SensorPlan, rngs):
    """The hourly state loop. Returns (records, lifts DataFrame, ledger snapshots)."""
    par, phys = build_params(cfg, fleet)
    box = ContainerFleet(par, phys, int(cfg["compaction"]["n_layers"]))
    state = _initial_state(cfg, fleet, box, rngs["init"])
    ocfg = cfg["operations"]
    disp = Dispatcher(fleet["threshold_pct"].to_numpy(), next_allowed_index(truck_allowed(tl, ocfg)),
                      ocfg, rngs["operations"])
    rec = _Recorder(tl.n_hours - tl.main_start, len(fleet))
    state["fill_est"] = None
    if cfg["sensors"].get("fill_mode", "ultrasonic") == "pressure_derived":
        state["fill_est"] = PressureFillEstimator(cfg, fleet, rngs["fill_est"], tl.n_hours)
    bin_idx = np.arange(len(fleet))
    lift_rows: list[dict[str, np.ndarray]] = []
    ledger_start = box.ledger()
    for h in range(tl.n_hours):
        main = h >= tl.main_start
        if h == tl.main_start:
            ledger_start = box.ledger()
        lifted, lift_flag = _do_lifts(h, box, disp, plan, state, ocfg, rngs, lift_rows, main)
        box.environment(inp.t_cont[h])
        box.load_chamber(inp.mass[h], inp.w_hyg[h], inp.w_carry_abs[h], inp.w_carry_free[h],
                         inp.vref[h], inp.rain[h], inp.snow[h])
        pressed = inp.presses[h] > 0
        demand, side = box.press(pressed, int(tl.day_idx[h]), inp.t_cont[h], inp.visc[h])
        box.relax_step(pressed)
        snap = box.snapshot()
        base = None
        if state["fill_est"] is not None:   # firmware: rolling average of back pressure
            p_true = np.minimum(par.relief, inp.visc[h] * (par.p_idle + demand))
            gain = pressure_gain(fleet, np.full(len(fleet), (h - tl.main_start) / 8766.0), bin_idx)
            base = state["fill_est"].update(h, inp.presses[h], p_true, gain, inp.oil[h], par.relief)
        reading, ff_active, unbridged = plan.read_fill(h, snap["fill_pct"], state["cancelled_ff"], base)
        disp.order(h, reading, state["age_h"], ff_active, snap["fill_pct"], unbridged)
        state["last_fill"] = snap["fill_pct"]
        state["side_since"] += side
        state["age_h"] += 1.0
        if main:
            rec.store(h - tl.main_start, {**snap, "fill_obs": reading, "demand": demand, "side_waste": side,
                                          "lifted": lifted, "ff_active": ff_active,
                                          "lift_event": lift_flag.astype(np.int8)})
    return rec.data, _lifts_frame(lift_rows, tl, fleet), {"start": ledger_start, "end": box.ledger()}


def _lifts_frame(rows: list[dict[str, np.ndarray]], tl: Timeline, fleet: pd.DataFrame) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    data = {k: np.concatenate([r[k] for r in rows]) for k in rows[0]}
    b = data.pop("bin_idx")
    lift_hour, order_hour = data.pop("lift_hour"), data.pop("order_hour")
    df = pd.DataFrame(data)
    df.insert(0, "bin_id", fleet["bin_id"].to_numpy()[b])
    df.insert(1, "ts", tl.ts[lift_hour])
    df.insert(2, "order_ts", tl.ts[np.maximum(order_hour, 0)])
    df["trigger"] = df["trigger"].map(TRIGGER_NAMES)
    df = df.rename(columns={"mass_kg": "true_mass_removed_kg"})
    water = df[["water_humidity_kg", "water_carried_kg", "water_hopper_kg", "free_water_kg", "ice_kg"]].sum(axis=1)
    base = df["base_kg"].to_numpy()
    df["true_moisture_extra"] = np.where(base > 0, water.to_numpy() / np.maximum(base, 1e-9), np.nan)
    attrs = fleet.set_index("bin_id")
    for col in ("material", "equipment_class", "container_volume_m3", "container_tare_kg",
                "infeed", "drainage", "region"):
        df[col] = attrs.loc[df["bin_id"], col].to_numpy()
    df["gross_vehicle_load_kg"] = df["true_mass_removed_kg"] + df["container_tare_kg"]
    return df.sort_values(["bin_id", "ts"]).reset_index(drop=True)


def _ledger_summary(ledger, inp: HourlyInputs, main: int) -> dict[str, np.ndarray]:
    """Per-bin mass balance over the reported period (residual should be ~0)."""
    start, end = ledger["start"], ledger["end"]
    material_in = (inp.mass[main:] + inp.w_hyg[main:] + inp.w_carry_abs[main:] + inp.w_carry_free[main:]).sum(0)
    precip_in = (inp.rain[main:] + inp.snow[main:]).sum(0)
    out = {k: end[k] - start[k] for k in ("drained", "seeped", "side_waste", "lifted")}
    residual = (start["mass_kg"] + material_in + precip_in - out["drained"] - out["seeped"]
                - out["side_waste"] - out["lifted"] - end["mass_kg"])
    return {"mass_start_kg": start["mass_kg"], "mass_end_kg": end["mass_kg"], "material_in_kg": material_in,
            "precip_in_kg": precip_in, **{f"{k}_kg": v for k, v in out.items()}, "residual_kg": residual}


def _hourly_inputs(cfg, tl, fleet, inflow, water, oil_end, rngs) -> HourlyInputs:
    hyd = cfg["hydraulics"]
    return HourlyInputs(
        mass=inflow.mass_kg, w_hyg=water.w_hyg, w_carry_abs=water.w_carry_abs,
        w_carry_free=water.w_carry_free,
        vref=load_reference_volume(cfg, tl, fleet, inflow, water, rngs["density"]),
        rain=water.hopper_rain_kg, snow=water.hopper_snow_kg,
        presses=inflow.presses, t_cont=water.container_temp,
        visc=viscosity_factor(oil_end, hyd["viscosity_coeff_per_c"], hyd["viscosity_ref_c"]),
        oil=oil_end,
    )


def run_simulation(cfg: Mapping[str, Any]) -> SimResult:
    """Run the full simulation in memory (no files written)."""
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    rngs = _rngs(cfg["run"]["seed"])
    tl = build_timeline(cfg)
    fleet = build_fleet(cfg, rngs["fleet"])
    wx = build_weather(cfg, tl, rngs["weather"])
    inflow = build_inflow(cfg, tl, fleet, rngs["inflow"])
    water = build_load_water(cfg, tl, wx, fleet, inflow)
    oil_start, oil_end = oil_temperature(water.machine_ambient, inflow.presses, cfg["hydraulics"]["oil"])
    inp = _hourly_inputs(cfg, tl, fleet, inflow, water, oil_end, rngs)
    plan = build_sensor_plan(cfg, tl, fleet, rngs["sensors"])
    timings["precompute_s"] = time.perf_counter() - t0
    log.info("precompute %.1f s (%d hours x %d bins)", timings["precompute_s"], tl.n_hours, len(fleet))

    t1 = time.perf_counter()
    records, lifts, ledger = run_loop(cfg, tl, fleet, inp, plan, rngs)
    timings["loop_s"] = time.perf_counter() - t1
    log.info("hourly loop %.1f s (%d lifts reported)", timings["loop_s"], len(lifts))

    t2 = time.perf_counter()
    comp = np.array([cfg["compaction"]["compressibility"][m] for m in fleet["material"]])
    strokes = build_strokes(cfg, tl, fleet, inflow.presses, records["demand"], oil_start, plan.dropout,
                            rngs["strokes"], bool(cfg["run"]["write_strokes"]), records["fill_pct"], comp)
    timings["strokes_s"] = time.perf_counter() - t2
    return SimResult(cfg=cfg, timeline=tl, fleet=fleet, weather=wx, inflow=inflow, water=water,
                     oil_end=oil_end, dropout=plan.dropout, records=records, lifts=lifts, strokes=strokes,
                     ledger=_ledger_summary(ledger, inp, tl.main_start), timings=timings)
