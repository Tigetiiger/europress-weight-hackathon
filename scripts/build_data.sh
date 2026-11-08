#!/usr/bin/env bash
# Rebuild every generated file from scratch on a fresh clone:
#   simulate the fleet -> validate -> baseline -> train -> batch-predict -> replay slice
#   -> technical docs site -> dashboard soft-sensor data.
# Takes ~15 min and ~6 GB RAM on 4 cores. All data is synthetic.
#
#   scripts/build_data.sh            # full run (400 machines x 365 days, seed 7)
#   PYTHON=python3.12 scripts/build_data.sh
#   SKIP_TRAIN=1 scripts/build_data.sh   # reuse an existing models/full400 (e.g. copied from another machine)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv}"
BINS="${BINS:-400}"
DAYS="${DAYS:-365}"
SEED="${SEED:-7}"
RUN="runs/full400"
MODELS="models/full400"

step() { printf '\n== %s  %s\n' "$(date +%H:%M:%S)" "$*"; }

if [ ! -x "$VENV/bin/python" ]; then
  step "creating virtualenv $VENV"
  "${PYTHON:-python3}" -m venv "$VENV"
fi
PY="$VENV/bin/python"
step "installing Python packages"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r "$ROOT/soft_sensor/requirements.txt"

cd "$ROOT/soft_sensor"
step "simulating $BINS machines x $DAYS days (seed $SEED)"
"$PY" -m compactor_sim --config config.yaml --bins "$BINS" --days "$DAYS" --seed "$SEED" --out "$RUN"
step "validating the simulated data"
"$PY" validate.py "$RUN"
step "baseline models"
"$PY" baseline.py "$RUN"
if [ "${SKIP_TRAIN:-0}" = "1" ] && [ -f "$MODELS/time/bundle.json" ]; then
  step "SKIP_TRAIN=1: reusing the trained models in $MODELS"
else
  step "training the soft sensor (the long step)"
  "$PY" -m weight_train train --data "$RUN" --out "$MODELS" --seed "$SEED"
fi
step "hourly batch predictions"
"$PY" -m weight_train predict --models "$MODELS" --input "$RUN/hourly.parquet" \
  --strokes "$RUN/strokes.parquet" --output "$MODELS/predictions.parquet"
step "replay slice for the dashboard (all machines, test period + 40-day lookback)"
"$PY" tools/make_replay_slice.py --src "$RUN" --dst runs/replay400 \
  --metrics "$MODELS/metrics.json" --predictions "$MODELS/predictions.parquet"
step "technical docs site"
"$PY" make_hires_plots.py
"$PY" make_site.py --model-run "$MODELS"

cd "$ROOT/dashboard"
step "dashboard soft-sensor data"
"$PY" pipeline/softsensor_data.py
cp public/data/softsensor/{evaluation,check,explainer}.json ai-workspace/data/softsensor/

step "done. Start everything with: scripts/start.sh"
