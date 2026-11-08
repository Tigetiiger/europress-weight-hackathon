# Hydraulic weight soft sensor — dashboard integration

**All data is synthetic** (physics-based simulator, `soft_sensor/compactor_sim`).
Nothing here is real Europress data or real Europress pressure settings.

The soft sensor estimates the **mass of waste (kg) inside a compactor every hour**, with a calibrated
p10–p90 band. It uses only signals the machine already has: fill estimate from back pressure, presses,
peak ram pressure, the 16-point pressure graph, plus public weather (air temperature, rain, humidity; no
oil-temperature sensor is assumed). The dashboard shows it under **Europress → Weight soft sensor** (`#ep-sensor`).

## Architecture

```
simulator telemetry (runs/replay400)    trained bundle (models/full400/time)
              │                                   │
              ▼                                   ▼
  softsensor/  Python API 127.0.0.1:8093  ── weight_train (same feature code as training)
              │   /meta /fleet?at= /machines/<BIN>?at=&hours=
              ▼
  server/server.mjs  GET /api/sensor/*  (whitelisted paths + params, 300 req/min/IP)
              ▼
  public/js/sensor/*  Live fleet · machine · Weighbridge check · Model · How it works
              ▲
  pipeline/softsensor_data.py → public/data/softsensor/*.json (evaluation, weighbridge check, explainer)
```

- **Replay clock.** The live pages step hour by hour through **1 Jul – 30 Sep 2026**. That is the
  `time` bundle's held-out test period, so every value shown is out-of-sample.
- **Fleet table.** Values come from the hourly batch job (`models/full400/predictions.parquet`). If
  that file is missing, the service scores the window itself at start-up.
- **Machine page.** Runs the model live for that hour, on the machine's telemetry since its last
  emptying plus 9 days of weather history (≈120 ms). It shows the batch value next to it: they are
  identical (verified on 60 random machine-hours, max difference 0.0 kg).
- **No simulator truth** reaches the dashboard: accuracy is shown against weighbridge tickets only. Telemetry passes `weight_train`'s column whitelist and leakage guard. Hidden
  machine parameters from `bins.parquet` are never loaded.

## Run

Easiest: `scripts/build_data.sh` once, then `scripts/start.sh` (both at the repo root) start this API
together with the web server. By hand, from `dashboard/`:

```bash
# 1. static data for Weighbridge check / Model / How it works (~10 s)
../.venv/bin/python pipeline/softsensor_data.py
# 2. model API (~15 s to load, ~1.8 GB RAM); reads ../soft_sensor/runs/replay400 + models/full400
../.venv/bin/python -m softsensor
#    or as a service (edit the absolute paths in the unit first):
cp deploy/europress-softsensor.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now europress-softsensor
# 3. tests (~15 s)
../.venv/bin/python -m pytest softsensor -q
```

Settings (env): `SOFTSENSOR_SIM_ROOT` (default `../soft_sensor`), `SOFTSENSOR_RUN` (default `replay400`:
all 400 machines, test period + 40-day lookback), `SOFTSENSOR_MODELS` (`full400`), `SOFTSENSOR_SPLIT`
(`time`), `SOFTSENSOR_HOST`/`SOFTSENSOR_PORT` (`127.0.0.1:8093`). Node: `SENSOR_URL`, `SENSOR_RATE_MAX`, `SENSOR_MAX_IN_FLIGHT`.

## API (all responses `{success, data, error}`)

| GET | returns |
|---|---|
| `/health` | `{ok, machines}` |
| `/meta` | replay window, model info, thresholds, public machine attributes |
| `/fleet?at=ISO` | per machine: fill, hours since emptying, `mass_kg`, `p10_kg`, `p90_kg`, `kg_m3`; totals (`mass_t`, `ready_to_empty`, `heavy`, `mean_fill_pct`) |
| `/machines/BIN-nnnn?at=ISO&hours=24…2880` | `live` (model run now, inputs, latency), `batch`, `now` (fill, hours since emptying), hourly `series` up to `at`, `lifts`: emptyings with estimate 1 h before vs weighbridge |

`at` is floored to the hour and must lie inside the replay window (omitted = window start); `hours` defaults to 336 and is clamped to 24…2880. The API never returns data after `at`.

## Caveats (also shown in the UI)

- Mixed-waste intervals on unseen machines are under-calibrated (33 % instead of 80 % coverage).
- 34 of ~20,300 simulated emptyings (mixed waste, 20 m³ DUO) exceed the ~10 t truck limit (simulator fix pending).
- A machine's first, incomplete cycle in the data is less accurate.
- Estimates are not legal-for-trade weighings.
