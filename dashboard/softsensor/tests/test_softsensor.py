"""Soft-sensor service tests on the 25-machine dev run (runs/bins25 + models/bins25).

Run:  /home/hackathon/simulator-weather-and-stuff/.venv/bin/python -m pytest softsensor -q
"""
from __future__ import annotations

import dataclasses
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np
import pandas as pd
import pytest

from softsensor.__main__ import build_api
from softsensor.api import ApiError, make_handler
from softsensor.config import Settings
from softsensor.stores import PUBLIC_MACHINE_COLUMNS, build_grid, cycle_ids, hours_since_lift, row_ranges

DEV = dataclasses.replace(Settings.from_env(), run="demo40", models="full400")  # shipped slice + weights


@pytest.fixture(scope="module")
def api():
    if not (DEV.run_dir / "hourly.parquet").is_file():
        pytest.skip(f"dev run missing: {DEV.run_dir} (set SOFTSENSOR_SIM_ROOT)")
    return build_api(DEV)


@pytest.fixture(scope="module")
def base_url(api):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=30) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


# ------------------------------------------------------------------ pure helpers
def test_row_ranges_splits_sorted_ids():
    assert row_ranges(pd.Series(["A", "A", "B", "C", "C"])) == {"A": (0, 2), "B": (2, 3), "C": (3, 5)}


def test_hours_since_lift_resets_per_machine():
    bins = np.array(["A"] * 4 + ["B"] * 3)
    lift = np.array([0, 1, 0, 0, 0, 0, 1])
    out = hours_since_lift(bins, lift)
    assert np.isnan(out[0]) and list(out[1:4]) == [0, 1, 2]
    assert np.isnan(out[4]) and np.isnan(out[5]) and out[6] == 0


def test_cycle_ids_start_at_lifts_and_machines():
    bins = np.array(["A", "A", "A", "B", "B"])
    assert list(cycle_ids(bins, np.array([0, 1, 0, 0, 1]))) == [1, 2, 2, 3, 4]


def test_grid_fill_is_not_carried_over_an_emptying():
    ts = pd.date_range("2026-07-01", periods=4, freq="h", tz="Europe/Helsinki")
    tel = pd.DataFrame({"bin_id": ["A"] * 4, "ts": ts, "fill_pct": [90.0, np.nan, np.nan, 5.0], "lift_event": [0, 0, 1, 0]})
    truth = pd.DataFrame({"bin_id": ["A"] * 4, "ts": ts, "true_mass_kg": [1.0] * 4})
    batch = pd.DataFrame({"bin_id": ["A"] * 4, "ts": ts, "mass_pred_kg": 1.0, "mass_p10_kg": 1.0, "mass_p90_kg": 1.0})
    g = build_grid(tel, truth, batch, ts[0])
    assert g.fill[0, 1] == 90.0           # dropout inside the cycle: forward-filled
    assert np.isnan(g.fill[0, 2])         # emptied: the old 90 % must not leak into the new cycle
    assert g.fill[0, 3] == 5.0


# ------------------------------------------------------------------ data hygiene
def test_telemetry_carries_no_truth(api):
    cols = set(api.data.telemetry.columns)
    assert not [c for c in cols if c.startswith("true_")]
    assert not cols & {"lifted_mass_kg", "side_waste_kg", "ref_scale_kg"}


def test_machines_expose_only_public_attributes(api):
    assert set(api.data.machines.columns) <= set(PUBLIC_MACHINE_COLUMNS)
    exposed = set(api.meta({})["machines"][0])
    assert not exposed & {"density_factor", "fill_gain_bias", "pressure_gain_bias", "threshold_pct", "k_machine"}


def test_replay_window_is_the_held_out_period(api):
    assert api.data.replay_start >= api.data.test_start
    assert api.data.replay_start - api.data.test_start < pd.Timedelta(hours=1)


# ------------------------------------------------------------------ the core claim: live == batch
def test_live_inference_reproduces_batch_values(api):
    g = api.data.grid
    rng = np.random.default_rng(7)
    for _ in range(8):
        i, j = rng.integers(len(g.bins)), rng.integers(len(g.hours))
        live = api.engine.estimate(str(g.bins[i]), g.hours[j])
        assert live["mass_kg"] == pytest.approx(float(g.pred[i, j]), rel=1e-4, abs=0.05)
        assert live["p10_kg"] <= live["mass_kg"] <= live["p90_kg"]


def test_live_uses_only_rows_up_to_at(api):
    g = api.data.grid
    at = g.hours[len(g.hours) // 2]
    rows, strokes = api.engine.window(str(g.bins[0]), at)
    assert rows["ts"].max() == at
    assert strokes is None or strokes["ts"].max() < at + pd.Timedelta(hours=1)


def test_live_results_are_cached(api):
    g = api.data.grid
    first = api.engine.estimate(str(g.bins[1]), g.hours[5])
    second = api.engine.estimate(str(g.bins[1]), g.hours[5])
    assert second["cached"] and second["mass_kg"] == first["mass_kg"]


# ------------------------------------------------------------------ handlers
def test_parse_at_validates_and_floors(api):
    start = api.data.replay_start
    assert api.parse_at({}) == start
    assert api.parse_at({"at": [(start + pd.Timedelta(minutes=30)).isoformat()]}) == start
    naive = start.tz_localize(None).isoformat()
    assert api.parse_at({"at": [naive]}) == start
    with pytest.raises(ApiError) as bad:
        api.parse_at({"at": ["not-a-date"]})
    assert bad.value.status == 400
    with pytest.raises(ApiError):
        api.parse_at({"at": ["2020-01-01T00:00:00+02:00"]})
    for dst in ("2026-10-25T03:30:00", "2026-03-29T03:30:00"):   # ambiguous / non-existent local time
        with pytest.raises(ApiError) as err:
            api.parse_at({"at": [dst]})
        assert err.value.status == 400


def test_parse_hours_bounds(api):
    assert api.parse_hours({"hours": ["1"]}) == 24
    assert api.parse_hours({"hours": ["999999"]}) == 24 * 120
    with pytest.raises(ApiError):
        api.parse_hours({"hours": ["-5"]})


def test_fleet_snapshot(api):
    out = api.fleet({})
    assert len(out["machines"]) == len(api.data.grid.bins)
    row = out["machines"][0]
    assert {"bin_id", "mass_kg", "p10_kg", "p90_kg", "fill_pct", "truth_kg"} <= set(row)
    assert out["totals"]["mass_t"] > 0
    assert 0 <= out["evaluation"]["coverage"] <= 1


def test_machine_series_stops_at_at(api):
    g = api.data.grid
    at = g.hours[400]
    out = api.machine(str(g.bins[2]), {"at": [at.isoformat()], "hours": ["168"]})
    t = out["series"]["t"]
    assert len(t) == 168 and t[-1] == at.value // 1_000_000
    assert all(lift["t"] <= t[-1] for lift in out["lifts"])
    assert out["live"]["mass_kg"] == pytest.approx(out["batch"]["mass_kg"], rel=1e-4, abs=0.1)


def test_unknown_machine_is_404(api):
    with pytest.raises(ApiError) as err:
        api.machine("BIN-9999", {})
    assert err.value.status == 404


# ------------------------------------------------------------------ HTTP envelope
def test_http_envelope(base_url):
    status, body = get(f"{base_url}/health")
    assert status == 200 and body["success"] and body["error"] is None
    status, body = get(f"{base_url}/nope")
    assert status == 404 and body == {"success": False, "data": None, "error": "not found"}
    status, body = get(f"{base_url}/fleet?at=garbage")
    assert status == 400 and not body["success"]


def test_http_machine(base_url, api):
    bin_id = str(api.data.grid.bins[0])
    status, body = get(f"{base_url}/machines/{bin_id}?hours=48")
    assert status == 200 and body["data"]["machine"]["bin_id"] == bin_id
    assert len(body["data"]["series"]["t"]) == 1  # at defaults to the first replay hour
