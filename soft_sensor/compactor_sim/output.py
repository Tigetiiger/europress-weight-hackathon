"""Write a run folder: Parquet tables, config_used.yaml and summary.json."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from .simulate import SimResult

log = logging.getLogger(__name__)


def _bin_major(a: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(a.T).ravel()


def _f32(a: np.ndarray) -> pa.Array:
    return pa.array(np.asarray(a, dtype=np.float32))


def _strings(codes: np.ndarray, labels: np.ndarray) -> pa.Array:
    """Plain string column built from integer codes (via a dictionary cast)."""
    dictionary = pa.array([str(x) for x in labels], type=pa.string())
    return pa.DictionaryArray.from_arrays(pa.array(codes.astype(np.int32)), dictionary).cast(pa.string())


def _static(res: SimResult, n_hours: int) -> dict[str, pa.Array]:
    fleet = res.fleet
    rows = np.repeat(np.arange(len(fleet)), n_hours)
    cols: dict[str, pa.Array] = {"bin_id": _strings(rows, fleet["bin_id"].to_numpy())}
    ts = np.tile(res.timeline.ts.asi8[res.timeline.main_start:], len(fleet))
    cols["ts"] = pa.array(ts, type=pa.timestamp("ns", tz=res.cfg["run"]["timezone"]))
    for name in ("material", "equipment_class"):
        labels, codes = np.unique(fleet[name].to_numpy(), return_inverse=True)
        cols[name] = _strings(codes[rows], labels)
    cols["container_volume_m3"] = pa.array(fleet["container_volume_m3"].to_numpy()[rows])
    for name in ("region", "infeed"):
        labels, codes = np.unique(fleet[name].to_numpy(), return_inverse=True)
        cols[name] = _strings(codes[rows], labels)
    cols["drainage"] = pa.array(fleet["drainage"].to_numpy(dtype=bool)[rows])
    return cols


def _region_cols(res: SimResult) -> dict[str, np.ndarray]:
    tl, wx = res.timeline, res.weather
    m = tl.main_start
    region_idx = np.array([wx.regions.index(r) for r in res.fleet["region"]])
    day = tl.day_idx[m:]
    return {
        "rh_pct": _bin_major(wx.rh_day[day][:, region_idx]),
        "temp_c": _bin_major(wx.temp_h[m:, region_idx]),
        "precip_mm_day": _bin_major(wx.precip_day[day][:, region_idx]),
        "precip_mm": _bin_major(wx.precip_h[m:, region_idx]),
        "snow_mm_day": _bin_major(wx.snow_day[day][:, region_idx]),
    }


def _ref_scale(res: SimResult) -> np.ndarray:
    m = res.timeline.main_start
    mass = res.records["mass_kg"]
    rng = np.random.default_rng(np.random.SeedSequence([int(res.cfg["run"]["seed"]), 99]))
    noisy = mass * (1 + res.cfg["sensors"]["ref_scale_noise"] * rng.standard_normal(mass.shape))
    ok = res.fleet["ref_scale"].to_numpy(dtype=bool)[None, :] & ~res.dropout[m:]
    return _bin_major(np.where(ok, noisy, np.nan))


def hourly_table(res: SimResult) -> pa.Table:
    tl, rec = res.timeline, res.records
    m = tl.main_start
    cols = _static(res, tl.n_hours - m)
    wc = _region_cols(res)
    cols.update({
        "rh_pct": _f32(wc["rh_pct"]),
        "temp_c": _f32(wc["temp_c"]),
        "precip_mm_day": _f32(wc["precip_mm_day"]),
        "fill_pct": _f32(_bin_major(rec["fill_obs"])),
        "presses": pa.array(_bin_major(res.inflow.presses[m:]).astype(np.int32)),
        "peak_pressure_bar": _f32(res.strokes.peak_bar),
        "lift_event": pa.array(_bin_major(rec["lift_event"]).astype(np.int8)),
        "true_mass_kg": _f32(_bin_major(rec["mass_kg"])),
        "true_fill_pct": _f32(_bin_major(rec["fill_pct"])),
        "true_density_kg_m3": _f32(_bin_major(rec["density"])),
        "true_moisture_extra": _f32(_bin_major(rec["moisture_extra"])),
        # optional signals and extra truth
        "oil_temp_c": _f32(_bin_major(res.oil_end[m:])),
        "energy_kwh": _f32(res.strokes.energy_kwh),
        "precip_mm": _f32(wc["precip_mm"]),
        "snow_mm_day": _f32(wc["snow_mm_day"]),
        "ref_scale_kg": _f32(_ref_scale(res)),
        "lifted_mass_kg": _f32(_bin_major(rec["lifted"])),
        "side_waste_kg": _f32(_bin_major(rec["side_waste"])),
        "true_water_humidity_kg": _f32(_bin_major(rec["w_hyg"])),
        "true_water_carried_kg": _f32(_bin_major(rec["w_car"])),
        "true_water_hopper_kg": _f32(_bin_major(rec["w_hop"])),
        "true_free_water_kg": _f32(_bin_major(rec["pool"])),
        "true_ice_kg": _f32(_bin_major(rec["ice"])),
        "true_false_full": pa.array(_bin_major(rec["ff_active"]).astype(bool)),
        # pressure graph (per-stroke curve features, hourly mean)
        **{name: _f32(col) for name, col in res.strokes.curve_hourly.items()},
    })
    return pa.table(cols)


def build_summary(res: SimResult, n_rows: int) -> dict[str, Any]:
    lifts, led = res.lifts, res.ledger
    per_material = {}
    if len(lifts):
        per_material = (lifts.groupby("material")["true_mass_removed_kg"].agg(["mean", "count"])
                        .round(1).to_dict(orient="index"))
    return {
        "seed": int(res.cfg["run"]["seed"]), "n_bins": len(res.fleet), "days": int(res.cfg["run"]["days"]),
        "start": res.cfg["run"]["start"], "hourly_rows": n_rows, "n_lifts": len(lifts),
        "lift_mass_by_material": per_material,
        "false_full_lift_share": float(lifts["false_full_trigger"].mean()) if len(lifts) else None,
        "mass_balance_kg": {k: float(np.sum(v)) for k, v in led.items()},
        "mass_balance_max_abs_residual_kg": float(np.max(np.abs(led["residual_kg"]))),
        "timings_s": {k: round(v, 2) for k, v in res.timings.items()},
        "note": "Synthetic data. Nothing here represents real Europress data.",
    }


def write_run(res: SimResult, out_dir: str | Path) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    table = hourly_table(res)
    pq.write_table(table, out / "hourly.parquet", compression="zstd", row_group_size=1_000_000)
    res.fleet.to_parquet(out / "bins.parquet", index=False)
    daily = res.weather.daily.assign(date=lambda d: d["date"].dt.date)
    daily.to_parquet(out / "weather_daily.parquet", index=False)
    res.lifts.to_parquet(out / "lifts.parquet", index=False)
    if res.strokes.curves_sample is not None:
        res.strokes.curves_sample.to_parquet(out / "pressure_curves.parquet", index=False, compression="zstd")
    if res.strokes.table is not None:
        res.strokes.table.to_parquet(out / "strokes.parquet", index=False, compression="zstd")
    with (out / "config_used.yaml").open("w", encoding="utf-8") as fh:
        yaml.safe_dump(dict(res.cfg), fh, sort_keys=False, allow_unicode=True)
    summary = build_summary(res, table.num_rows)
    with (out / "summary.json").open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)
    log.info("wrote %s (%d hourly rows)", out, table.num_rows)
    return summary
