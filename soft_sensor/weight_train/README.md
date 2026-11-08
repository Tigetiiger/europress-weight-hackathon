# weight_train — hydraulic soft sensor (container mass every hour)

Learns to estimate the mass of waste (kg) inside a compactor every hour from signals
the machine already has: fill level, press count and ram pressure (incl. the pressure graph),
plus public weather. The value one hour before each emptying is the number a customer
compares with the transporter's weighbridge. It is trained on simulator output
(`runs/<name>/`). **All data is synthetic.**

Results, tables and plots for a trained run are in `models/<run>/REPORT.md`.

## Run

```bash
.venv/bin/pip install -r requirements.txt

# train + evaluate both splits, write models/<run>/ (incl. plots + REPORT.md)
.venv/bin/python -m weight_train train --data runs/full400 --out models/full400 [--split bins|time|both] [--setup per_material|shared|both] [--seed 7]
.venv/bin/python -m weight_train train --data runs/bins25  --out models/bins25

# predict hourly mass + p10/p90 (input needs no true_ columns; strokes.parquet next to it is used if present)
.venv/bin/python -m weight_train predict --models models/full400 --input runs/full400/hourly.parquet --output predictions.parquet [--split time|bins]

# rebuild plots and REPORT.md
.venv/bin/python -m weight_train report --models models/full400

.venv/bin/python -m pytest -q tests/test_weight_train.py
```
`predict` writes `bin_id, ts, material, mass_pred_kg, mass_p10_kg, mass_p90_kg` and prints
wall time, peak memory and µs per machine-hour. It uses the `time` (pilot-fleet) bundle
by default. The `bins` bundle is the one validated on unseen machines.

## Method

* **Target**: kg per m³ of container (`true_mass_kg / container_volume_m3`), multiplied back by
  the volume at prediction time. One model can then cover all container sizes.
* **Models**: LightGBM regressors.
  * *Shared*: one model for all materials, with `material` as a categorical feature.
  * *Per material*: one model per material, using the shared model's tuned parameters.
  * The better setup is chosen **per material on the validation slice** and never on test.
  * Tuning is a 4-combination grid (`num_leaves` × `min_data_in_leaf`) on the shared model,
    with early stopping on validation.
  * Monotone constraints (+ on `fill_ffill` and `presses_cycle`) are kept when they do not
    worsen validation error by more than 2 %.
* **Training rows**: every pre-lift hour plus a random sample of other hours (cap 200 k rows per
  model). Pre-lift hours get weight 5, because that is the number that is compared.
* **Interval**:
  * Quantile models for p10 and p90.
  * **Normalised conformalised quantile regression** on a separate calibration slice:
    score = max(p10 − y, y − p90) / width. Its finite-sample 80 % quantile Q widens the band
    to [p10 − Q·w, p90 + Q·w].
  * Calibration is Mondrian by material × fill bucket (<50, 50–80, ≥80 %), falling back to the
    material and then the pool when a cell has fewer than 30 points.
* **Splits** (both reported):
  * `bins`, the new-installation case: 25 % of machines, stratified by material, are tested on
    the last 25 % of the period. Validation and calibration use *other* held-out machines.
  * `time`, the pilot-fleet case: all machines are trained on the first 75 % of time. The last
    20 % of that window is split by machine into validation and calibration, and the remaining
    25 % of time is the test.
  * The first, incomplete cycle of every machine is excluded.
* **No leakage**:
  * Input columns are whitelisted (`columns.py`).
  * `assert_no_leakage` rejects any `true_*` column or hidden machine truth at feature-build
    and fit time.
  * Tests check that scrambling `true_*` changes nothing, and that cutting or scrambling the
    future leaves all earlier features identical.
  * Daily weather comes only from *completed* days, because today's daily total contains
    future hours.

## Features (per machine and hour, backward-looking only)

A *cycle* starts at each `lift_event` hour. "Since lift" means within the current cycle.

| feature | meaning |
|---|---|
| `hours_since_lift` | hours since the container was last emptied |
| `fill_ffill` | reported fill % (on real machines a firmware estimate from ram pressure, so approximate), forward-filled over dropouts within the cycle |
| `fill_max_cycle` | highest fill reading since the lift (catches bridging spikes) |
| `fill_change_24h` | fill now minus 24 h ago |
| `naive_kg_m3` | fill × nominal material density (190/330/140/400 kg/m³; mixed waste 400), the naive estimate |
| `presses` | press cycles this hour |
| `presses_cycle` | press cycles since the lift, a proxy for the number of loads |
| `presses_per_fill` | presses since lift per % of fill (high means dense material) |
| `p_now` | peak ram pressure this hour (NaN without a stroke) |
| `p_last` | last observed peak pressure in the cycle |
| `p_ewm` | exponentially smoothed peak pressure (half-life 6 observations) within the cycle |
| `p_max_cycle` | max peak pressure since the lift |
| `p_count_cycle` | number of hours with a pressure reading since the lift |
| `rho_p` | pressure-implied pack density: inverts P/visc(T_air) − 25 bar (air temperature as the oil-viscosity proxy; no oil-temperature input) = k·fill^a·ρ^b. The per-material k, a, b are fitted on training rows from the label, and it is used only when fill > 25 % and P < 228 bar |
| `rho_p_smooth` | `rho_p` smoothed within the cycle (EWMA, half-life 4) |
| `pressure_kg_m3` | fill × `rho_p_smooth`, the physics-based kg/m³ estimate |
| `stroke_peak_mean` | mean per-stroke peak pressure this hour (from strokes.parquet) |
| `stroke_duration_mean` | mean stroke duration this hour (longer at high load) |
| `energy_kwh` | motor energy this hour |
| `curve_contact_pos` | pressure graph: ram position (0–1) where pressure starts rising this hour, i.e. where the pack face is |
| `curve_contact_ewm` | contact position smoothed within the cycle (EWMA, half-life 4) |
| `curve_contact_min_cycle` | earliest contact position since the lift (the pack only grows) |
| `curve_rise_slope_ewm` | smoothed slope from contact to peak (stiffer/denser pack rises faster) |
| `curve_mean_bar_ewm` | smoothed mean pressure over the stroke (∝ hydraulic work) |
| `curve_completion_min_cycle` | smallest share of the stroke completed before the relief valve opened |
| `graph_kg_m3` | geometric fill from the contact point (nominal 30 % at the stroke end … 100 % at 30 % travel) × nominal density |
| `temp_c` | air temperature this hour |
| `rh_prev_day` | yesterday's mean relative humidity |
| `rh_mean_cycle` | mean of `rh_prev_day` since the lift |
| `rain_mm_d1` … `rain_mm_d7` | precipitation 1…7 days ago (completed days, one column each) |
| `rain_72h` | precipitation in the last 72 hours (hourly data) |
| `rain_exposed_cycle` | rain since lift × infeed exposure (open 1.0, lidded 0.1, indoor 0) |
| `frost_days_cycle` | hours below 0 °C since the lift / 24 |
| `container_volume_m3` | container volume |
| `equipment_class` | COMBI or DUO |
| `infeed` | open_hopper / lidded_hopper / indoor_shaft |
| `drainage` | container has a drainage system |
| `hour`, `weekday` | local calendar. Month is deliberately not used, because test months are unseen |
| `material` | material (shared model only) |

Feature sets for the comparison ladder: `fill_presses` = machine, calendar, cycle, fill and
presses. `pressure` adds peak-pressure and stroke features. `pressure_graph` adds the pressure-graph
features. `full` adds weather. The reported `fill_pct` is itself only the machine's approximate,
pressure-derived estimate (simulator `sensors.fill_mode: pressure_derived`); the pressure graph gives an
independent, geometric view of the fill level.

## Outputs (`models/<run>/`)

`metrics.json` (all numbers) · `comparison.csv` (ladder per split × step × material) ·
`learning_curve.csv` · `feature_list.json` · `plots/*.png` · `REPORT.md` ·
`<split>/bundle.json` + LightGBM text models (`<setup>__<material|all>__<point|q_lo|q_hi>.txt`) ·
`<split>/test_predictions.parquet` · `<split>/shap_<material>.parquet` (exact TreeSHAP from
LightGBM `pred_contrib`, so the `shap` package is not needed) · `predict_stats.json`.

## Compute

CPU only, deterministic (`deterministic=True`, fixed seeds). Same data and seed give
identical metrics (tested). Wall time, peak memory and inference cost are printed and
recorded in REPORT.md.

## Licences of the libraries used

| library | licence |
|---|---|
| numpy | BSD-3-Clause |
| pandas | BSD-3-Clause |
| pyarrow (Apache Arrow) | Apache-2.0 |
| LightGBM | MIT |
| scikit-learn (in the allowed stack; not imported by this package) | BSD-3-Clause |
| matplotlib | Matplotlib License (PSF-style, BSD-compatible) |
| PyYAML (simulator config) | MIT |
| pytest (tests) | MIT |
