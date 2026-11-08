# Europress SMART Weight — Europress × Since AI Hackathon 2026

An hourly estimate of the waste mass inside each press-container compactor, using only signals
the machine already has (fill, press counts, ram pressure, oil temperature) plus public weather.
**All data is synthetic.** Nothing here is real Europress data or real Europress pressure settings.

## Layout

| Path | What |
|---|---|
| `dashboard/` | Node server (no dependencies) + static site: landing page `/`, dashboard `/dashboard/`, AI technical docs `/docs/` |
| `dashboard/softsensor/` | Python API that serves live soft-sensor estimates to the dashboard |
| `soft_sensor/compactor_sim/` | Physics-based simulator that generates the synthetic fleet |
| `soft_sensor/weight_train/` | Training and prediction pipeline (LightGBM) |
| `soft_sensor/site/` | Generated technical documentation and business model page |

## Run

```bash
node dashboard/server/server.mjs   # http://localhost:8080
```

The landing page and docs work without anything else. The live weight views also need the
soft-sensor API (`dashboard/softsensor/README.md`).

## Not in the repo

Model weights, simulator runs and some large generated files are git-ignored (see `.gitignore`).
To rebuild them, follow `soft_sensor/README.md`: simulate a run, train with `weight_train`,
then run `make_site.py` and `make_hires_plots.py`.
