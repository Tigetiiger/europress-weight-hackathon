"""Behavioural tests for the compactor simulator (small fleet, short horizon)."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from compactor_sim.cli import main as cli_main
from compactor_sim.config import MATERIALS, load_config, validate_config, with_run_overrides
from compactor_sim.moisture import emc_pct
from compactor_sim.simulate import run_simulation
from compactor_sim.timeline import easter_sunday, finnish_holidays

REQUIRED_HOURLY = {
    "bin_id": "string", "ts": "timestamp", "material": "string", "equipment_class": "string",
    "container_volume_m3": "double", "region": "string", "infeed": "string", "drainage": "bool",
    "rh_pct": "float", "temp_c": "float", "precip_mm_day": "float", "fill_pct": "float", "presses": "int32",
    "peak_pressure_bar": "float", "lift_event": "int8", "true_mass_kg": "float", "true_fill_pct": "float",
    "true_density_kg_m3": "float", "true_moisture_extra": "float", "oil_temp_c": "float",
}


def small_cfg(seed: int = 3, days: int = 45, bins: int = 25) -> dict:
    cfg = load_config(n_bins=bins, days=days, seed=seed)
    cfg["run"]["burn_in_days"] = 10
    return cfg


@pytest.fixture(scope="module")
def result():
    return run_simulation(small_cfg())


# ----------------------------------------------------------------------------- physics invariants
def test_mass_balance_closes_per_bin(result):
    led = result.ledger
    scale = led["material_in_kg"] + led["mass_start_kg"] + 1.0
    assert np.all(np.abs(led["residual_kg"]) < 1e-6 * scale)


def test_mass_balance_terms_are_meaningful(result):
    led = result.ledger
    assert led["lifted_kg"].sum() > 0.5 * led["material_in_kg"].sum()
    assert np.all(led["drained_kg"] >= 0) and np.all(led["side_waste_kg"] >= 0)


def test_no_negative_mass(result):
    rec = result.records
    assert rec["mass_kg"].min() >= -1e-6
    assert rec["pool"].min() >= -1e-9 and rec["ice"].min() >= -1e-9
    assert (result.lifts["true_mass_removed_kg"] > 0).all()
    assert (result.lifts["base_kg"] > 0).all()


def test_true_and_observed_fill_within_0_100(result):
    true_fill = result.records["fill_pct"]
    assert true_fill.min() >= 0.0 and true_fill.max() <= 100.0 + 1e-4
    obs = result.records["fill_obs"]
    obs = obs[~np.isnan(obs)]
    assert obs.min() >= 0.0 and obs.max() <= 100.0


def test_pressure_below_relief_cap_plus_noise(result):
    fleet = result.fleet
    peak = result.strokes.peak_bar.reshape(len(fleet), -1)
    gain = 1 + fleet["pressure_gain_bias"].abs() + fleet["pressure_gain_drift_per_year"].abs()
    cap = (fleet["relief_bar"] * gain).to_numpy()[:, None] + 6 * result.cfg["sensors"]["pressure"]["noise_sd_bar"]
    assert np.nanmax(peak - cap) <= 0
    presses = result.inflow.presses[result.timeline.main_start:].T
    assert np.all(np.isnan(peak[presses == 0]))


def test_pressure_carries_density_information_at_high_fill(result):
    peak = result.strokes.peak_bar.reshape(len(result.fleet), -1).T
    fill, dens = result.records["fill_pct"], result.records["density"]
    sel = ~np.isnan(peak) & (fill > 85)
    assert np.corrcoef(peak[sel], dens[sel])[0, 1] > 0.3


# ----------------------------------------------------------------------------- operations
def test_lifts_only_in_allowed_hours(result):
    ts = result.lifts["ts"]
    assert ts.dt.hour.between(6, 17).all()
    assert (ts.dt.day_of_week != 6).all()
    holidays = finnish_holidays(range(2025, 2027), result.cfg["calendar"]["holidays"])
    assert not ts.dt.date.isin(holidays).any()


def test_lift_events_match_lift_table(result):
    rec = result.records
    assert int(rec["lift_event"].sum()) == len(result.lifts)
    assert np.isclose(rec["lifted"].sum(), result.lifts["true_mass_removed_kg"].sum(), rtol=1e-5)


def test_lift_follows_order_by_at_least_min_delay(result):
    lag_h = (result.lifts["ts"] - result.lifts["order_ts"]).dt.total_seconds() / 3600
    assert (lag_h >= result.cfg["operations"]["truck_delay_h"][0]).all()


def test_combi_empties_fully_duo_keeps_small_residue(result):
    lifts = result.lifts
    combi = lifts[lifts["equipment_class"] == "COMBI"]
    duo = lifts[lifts["equipment_class"] == "DUO"]
    assert (combi["residue_kg"] == 0).all()
    share = duo["residue_kg"] / (duo["residue_kg"] + duo["base_kg"])
    assert (share <= 0.021).all()


# ----------------------------------------------------------------------------- reproducibility & fleet
def test_same_seed_gives_identical_output(result):
    again = run_simulation(small_cfg())
    for key, arr in result.records.items():
        np.testing.assert_array_equal(arr, again.records[key], err_msg=key)
    pd.testing.assert_frame_equal(result.lifts, again.lifts)
    np.testing.assert_array_equal(result.strokes.peak_bar, again.strokes.peak_bar)


def test_different_seed_changes_output(result):
    other = run_simulation(small_cfg(seed=4))
    assert not np.array_equal(result.records["mass_kg"], other.records["mass_kg"])


def test_small_fleet_has_every_material(result):
    fleet = result.fleet
    assert set(fleet["material"]) == set(MATERIALS)


# ----------------------------------------------------------------------------- building blocks
def test_finnish_holidays_2026():
    hcfg = load_config()["calendar"]["holidays"]
    hol = finnish_holidays([2026], hcfg)
    assert easter_sunday(2026) == dt.date(2026, 4, 5)
    for day in (dt.date(2026, 4, 3), dt.date(2026, 5, 14), dt.date(2026, 6, 19),
                dt.date(2026, 6, 20), dt.date(2026, 10, 31), dt.date(2026, 12, 6)):
        assert day in hol


def test_emc_curve_matches_anchor_points():
    e = load_config()["moisture"]["emc_oswin"]
    assert emc_pct(np.array(65.0), e["A"], e["B"], e["rh_max"]) == pytest.approx(7.0, abs=0.3)
    assert emc_pct(np.array(95.0), e["A"], e["B"], e["rh_max"]) == pytest.approx(14.0, abs=0.5)
    assert emc_pct(np.array(30.0), e["A"], e["B"], e["rh_max"]) < 5.0


def test_invalid_config_is_rejected():
    cfg = with_run_overrides(load_config(), n_bins=0)
    with pytest.raises(ValueError, match="n_bins"):
        validate_config(cfg)


def test_fmi_csv_replay_reproduces_daily_weather(result, tmp_path):
    daily = result.weather.daily
    csv = tmp_path / "fmi.csv"
    daily[["region", "date", "temp_mean_c", "precip_mm", "rh_pct"]].to_csv(csv, index=False)
    cfg = small_cfg()
    cfg["weather"]["mode"] = "fmi_csv"
    cfg["weather"]["fmi_csv"]["path"] = str(csv)
    replay = run_simulation(cfg).weather.daily
    for col in ("temp_mean_c", "precip_mm", "rh_pct"):
        np.testing.assert_allclose(replay[col], daily[col], rtol=1e-9, atol=1e-9)


def test_cli_writes_all_files_with_required_columns(tmp_path):
    import pyarrow.parquet as pq
    out = tmp_path / "run"
    assert cli_main(["--bins", "25", "--days", "20", "--seed", "5", "--out", str(out)]) == 0
    for name in ("hourly.parquet", "bins.parquet", "weather_daily.parquet", "lifts.parquet",
                 "strokes.parquet", "config_used.yaml", "summary.json"):
        assert (out / name).is_file(), name
    schema = pq.read_schema(out / "hourly.parquet")
    for col, kind in REQUIRED_HOURLY.items():
        assert col in schema.names, col
        assert str(schema.field(col).type).startswith(kind), (col, schema.field(col).type)
    hourly = pd.read_parquet(out / "hourly.parquet", columns=["bin_id", "ts"])
    assert len(hourly) == 25 * 20 * 24
    assert str(hourly["ts"].dt.tz) == "Europe/Helsinki"


def test_pressure_graph_contact_point_tracks_true_fill(result):
    curve = result.strokes.curve_hourly["curve_contact_pos"].reshape(len(result.fleet), -1).T
    fill = result.records["fill_pct"]
    sel = ~np.isnan(curve) & (fill >= 35)
    # the ram enters only ~20-30 cm into the container, so the rise starts only a little earlier as it fills
    assert np.corrcoef(curve[sel], fill[sel])[0, 1] < -0.2
    assert np.nanmin(curve) >= 0 and np.nanmax(curve) <= 1


def test_fill_reading_is_pressure_derived_and_approximate(result):
    obs, true_fill = result.records["fill_obs"], result.records["fill_pct"]
    ok = ~np.isnan(obs)
    err = obs[ok] - true_fill[ok]
    assert result.cfg["sensors"]["fill_mode"] == "pressure_derived"
    assert 2.0 < np.mean(np.abs(err[true_fill[ok] >= 30])) < 9.0   # ~5 % accuracy
    assert np.corrcoef(obs[ok], true_fill[ok])[0, 1] > 0.8
