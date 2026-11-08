# Europress SMART Weight — Europress × Since AI Hackathon 2026

An hourly estimate of the waste mass inside each press-container compactor, using only signals
the machine already has (fill, press counts, ram pressure) plus public weather.
**All data is synthetic.** Nothing here is real Europress data or real Europress pressure settings.

## Quick start (fresh clone)

Needs Python 3.11+, Node 20+, ~6 GB RAM and ~15 minutes for the first build. The AI advisor
(`/api/ask`) also needs the Claude Code CLI (`claude` on `PATH`, or set `CLAUDE_BIN`); everything
else works without it.

```bash
scripts/build_data.sh   # venv + packages, simulate 400 machines x 1 year, train, predict, build docs + data
scripts/start.sh        # soft-sensor API + web server on http://localhost:8080
```

| URL | Page |
|---|---|
| `/` | Landing page |
| `/dashboard/` | Dashboard (customer, partner and Europress views) |
| `/docs/` | AI technical documentation |
| `/redhat/` | Red Hat OpenShift AI training proof, if the separate `redhat_proof` folder sits next to this repo (or set `REDHAT_PROOF_DIR`) |

`build_data.sh` options (env): `BINS`, `DAYS`, `SEED`, `PYTHON`, `VENV`, `SKIP_TRAIN=1` (reuse an existing
`models/full400`). `start.sh` options: `PORT`, `HOST`, `SOFTSENSOR_PORT`, `VENV`.

## Layout

| Path | What |
|---|---|
| `scripts/` | `build_data.sh` (regenerate everything) and `start.sh` (run everything) |
| `dashboard/server/` | Node web server, no dependencies: landing page, dashboard, docs, API proxy, AI advisor |
| `dashboard/public/` | Landing page (`index.html`) and dashboard app (`dashboard/`, `js/`, `css/`, `data/`) |
| `dashboard/softsensor/` | Python API that serves live soft-sensor estimates to the dashboard |
| `soft_sensor/compactor_sim/` | Physics-based simulator that generates the synthetic fleet |
| `soft_sensor/weight_train/` | Training and prediction pipeline (LightGBM) |
| `soft_sensor/site/` | Generated technical documentation and business model page |

## Generated data

Git ignores the large outputs `build_data.sh` regenerates (see `.gitignore`); the small reports
(`metrics.json`, `REPORT.md`, the docs site, the dashboard JSON) are committed and get overwritten:

- `soft_sensor/runs/full400/`: one simulated year, 400 machines (~215 MB)
- `soft_sensor/runs/replay400/`: the slice the dashboard replays: all 400 machines, Jul–Sep 2026 test period plus a 40-day lookback (~76 MB)
- `soft_sensor/models/full400/`: model weights, SHAP tables, plots and hourly batch predictions

The dashboard's customer, partner and Europress views use pre-built JSON in `dashboard/public/data/`, made by
`dashboard/pipeline/build_data.py` from the Since AI competition data pack (not shipped; set `EUROPRESS_PACK`).
