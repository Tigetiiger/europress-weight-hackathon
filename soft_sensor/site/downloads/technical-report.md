# Hydraulic soft sensor - container mass report (`full400`)

On the `time` (same machines, later months) split the selected soft sensor estimates the mass in the hour before emptying with a mean error of **3.8 %** (median 3.1 %, 72 % of lifts within 5 %, n = 4876), against 28.3 % for the naive fill × nominal density estimate; 4 of 4 materials meet the < 5 % target. The calibrated p10–p90 interval covers 78 % of lift masses (target 80 %). On the `bins` (unseen machines) split the selected soft sensor estimates the mass in the hour before emptying with a mean error of **5.2 %** (median 4.4 %, 55 % of lifts within 5 %, n = 1221), against 32.1 % for the naive fill × nominal density estimate; 1 of 4 materials meet the < 5 % target (missing: cardboard, paper, mixed_waste). The calibrated p10–p90 interval covers 78 % of lift masses (target 80 %).

> **All data is synthetic** (simulator output for the Europress hackathon). The numbers demonstrate the method, not field performance.

![mean % error at lift by material](plots/lift_error_by_material.png)

## Compute

| item | value |
|---|---|
| training wall time (all splits) | 911 s |
| feature build | 10.0 s |
| peak memory (RSS) | 4.49 GB |
| hourly rows / machines | 3,504,000 / 400 |
| split `time` wall time | 550 s |
| split `bins` wall time | 350 s |
| prediction run | 3,504,000 rows in 81.0 s (features + predict 79.5 s) |
| prediction cost | 22.7 µs per machine-hour |
| prediction peak memory | 3.97 GB |

## Split `time` (same machines, later months)

Same machines, trained on the first 75 % of time and tested on the last 25 %. Training machines: 400, test machines: 400, test from 2026-07-01 17:15:00+03:00. Rows: train 2,070,084, val 262,800, cal 262,800, test 876,000.

Selected setup: cardboard → **per_material**, paper → **shared**, plastic_film → **per_material**, mixed_waste → **per_material**.

**At lift (hour before emptying vs weighbridge):**

| material | n | MAE kg | mean % | median % | within 5 % | within 10 % | bias kg | coverage | width kg |
|---|---|---|---|---|---|---|---|---|---|
| cardboard | 2479 | 121 | 4.0 | 3.3 | 69 % | 95 % | -42 | 78 % | 412 |
| paper | 702 | 139 | 4.3 | 3.4 | 68 % | 92 % | -0 | 80 % | 475 |
| plastic_film | 1168 | 58 | 3.3 | 2.6 | 78 % | 97 % | -2 | 79 % | 216 |
| mixed_waste | 527 | 147 | 3.1 | 2.5 | 83 % | 97 % | -9 | 77 % | 530 |
| **overall** | 4876 | 112 | 3.8 | 3.1 | 72 % | 95 % | -23 | 78 % | 387 |

**Hourly (every test hour):**

| material | MAE kg | mean % | coverage |
|---|---|---|---|
| cardboard | 74 | 5.6 | 80 % |
| paper | 83 | 5.9 | 78 % |
| plastic_film | 36 | 4.4 | 79 % |
| mixed_waste | 84 | 4.3 | 82 % |
| overall | 67 | 5.2 | 80 % |

![predicted vs true at lift, time](plots/pred_vs_true_time.png)

## Split `bins` (unseen machines)

Unseen machines (25 % held out, stratified by material), tested on the last 25 % of the period. Training machines: 240, test machines: 100, test from 2026-07-01 17:15:00+03:00. Rows: train 1,556,196, val 201,569, cal 188,497, test 219,000.

Selected setup: cardboard → **per_material**, paper → **per_material**, plastic_film → **per_material**, mixed_waste → **per_material**.

**At lift (hour before emptying vs weighbridge):**

| material | n | MAE kg | mean % | median % | within 5 % | within 10 % | bias kg | coverage | width kg |
|---|---|---|---|---|---|---|---|---|---|
| cardboard | 610 | 148 | 5.2 | 4.4 | 57 % | 89 % | -59 | 84 % | 564 |
| paper | 184 | 199 | 5.5 | 4.5 | 53 % | 83 % | -24 | 88 % | 935 |
| plastic_film | 294 | 76 | 4.6 | 3.8 | 62 % | 93 % | 4 | 81 % | 276 |
| mixed_waste | 133 | 392 | 6.3 | 6.0 | 39 % | 88 % | -159 | 34 % | 532 |
| **overall** | 1221 | 165 | 5.2 | 4.4 | 55 % | 89 % | -49 | 78 % | 547 |

**Hourly (every test hour):**

| material | MAE kg | mean % | coverage |
|---|---|---|---|
| cardboard | 90 | 6.6 | 82 % |
| paper | 106 | 6.6 | 84 % |
| plastic_film | 48 | 5.9 | 77 % |
| mixed_waste | 171 | 6.3 | 59 % |
| overall | 90 | 6.4 | 79 % |

![predicted vs true at lift, bins](plots/pred_vs_true_bins.png)

## Comparison ladder (mean % error at lift)

Each step adds signals: naive fill × nominal density → fill + press counts (LightGBM) → + peak hydraulic pressure → + pressure graph (pressure along the ram stroke: contact point, rise slope, mean pressure, completion) → + weather (full model, shared across materials); then the full per-material models and the setup selected per material on validation. The reported fill level itself is the machine's approximate, pressure-derived estimate.

**`time` (same machines, later months)**

| step | cardboard | paper | plastic_film | mixed_waste | **overall** |
|---|---|---|---|---|---|
| naive fill x density | 26.4 | 25.4 | 33.0 | 30.8 | 28.3 |
| fill + presses | 5.9 | 6.0 | 5.9 | 5.3 | 5.9 |
| + pressure (peak) | 4.6 | 4.6 | 3.8 | 3.7 | 4.3 |
| + pressure graph | 4.2 | 4.4 | 3.6 | 3.5 | 4.0 |
| + weather (full, shared) | 4.2 | 4.3 | 3.6 | 3.4 | 4.0 |
| full, per material | 4.0 | 4.0 | 3.3 | 3.1 | 3.8 |
| selected | 4.0 | 4.3 | 3.3 | 3.1 | 3.8 |

**`bins` (unseen machines)**

| step | cardboard | paper | plastic_film | mixed_waste | **overall** |
|---|---|---|---|---|---|
| naive fill x density | 31.0 | 28.3 | 41.9 | 20.3 | 32.1 |
| fill + presses | 6.2 | 6.7 | 7.6 | 9.5 | 7.0 |
| + pressure (peak) | 5.8 | 5.7 | 4.8 | 7.5 | 5.7 |
| + pressure graph | 5.4 | 5.3 | 4.8 | 6.7 | 5.4 |
| + weather (full, shared) | 5.1 | 5.1 | 4.7 | 6.9 | 5.2 |
| full, per material | 5.2 | 5.5 | 4.6 | 6.3 | 5.2 |
| selected | 5.2 | 5.5 | 4.6 | 6.3 | 5.2 |

![comparison ladder](plots/ladder.png)

## Per-material vs shared model

Setup is chosen per material on the validation lifts (mean % error); `–` means no validation lifts of that material, in which case the shared model is kept.

| material | split | val shared | val per-material | test shared | test per-material | selected | better on test |
|---|---|---|---|---|---|---|---|
| cardboard | time | 4.1 | 3.9 | 4.2 | 4.0 | per_material | per_material |
| paper | time | 3.8 | 3.9 | 4.3 | 4.0 | shared | per_material |
| plastic_film | time | 3.7 | 3.2 | 3.6 | 3.3 | per_material | per_material |
| mixed_waste | time | 3.6 | 3.0 | 3.4 | 3.1 | per_material | per_material |
| cardboard | bins | 4.6 | 4.3 | 5.1 | 5.2 | per_material | shared |
| paper | bins | 5.7 | 5.7 | 5.1 | 5.5 | per_material | shared |
| plastic_film | bins | 4.9 | 4.7 | 4.7 | 4.6 | per_material | per_material |
| mixed_waste | bins | 6.6 | 6.1 | 6.9 | 6.3 | per_material | per_material |

## Interval coverage (target 80 %)

Intervals come from LightGBM quantile models (p10/p90) calibrated by **normalised conformalised quantile regression (CQR)**: on a separate calibration slice (machines/hours used neither for training nor for validation) the score max(q_lo − y, y − q_hi) / width is computed and the interval is widened by its finite-sample 80 % quantile. Calibration is **Mondrian** by material × fill bucket (fill edges 50.0, 80.0 %); strata with fewer than 30 calibration points fall back to the material, then to the pool.

| material | split | lift coverage | lift width kg | hourly coverage | hourly width kg |
|---|---|---|---|---|---|
| cardboard | time | 78 % | 412 | 80 % | 261 |
| paper | time | 80 % | 475 | 78 % | 279 |
| plastic_film | time | 79 % | 216 | 79 % | 131 |
| mixed_waste | time | 77 % | 530 | 82 % | 344 |
| overall | time | 78 % | 387 | 80 % | 239 |
| cardboard | bins | 84 % | 564 | 82 % | 315 |
| paper | bins | 88 % | 935 | 84 % | 437 |
| plastic_film | bins | 81 % | 276 | 77 % | 151 |
| mixed_waste | bins | 34 % | 532 | 59 % | 391 |
| overall | bins | 78 % | 547 | 79 % | 300 |

Lift coverage more than 5 pp below target: mixed_waste/bins (34 %). Calibration pools all hours of a stratum, while the lift hour is the fullest, hardest hour of a cycle; on unseen machines the calibration machines are also few, so their error scale need not transfer.

![interval coverage](plots/coverage_by_material.png)

## Hourly mass curves

![hourly mass curves](plots/mass_curves_time.png)

## Error breakdowns (lift, selected model)

**Infeed type**

| infeed | time mean % | time n | bins mean % | bins n |
|---|---|---|---|---|
| indoor_shaft | 3.9 | 1611 | 5.2 | 286 |
| lidded_hopper | 3.8 | 1167 | 4.9 | 426 |
| open_hopper | 3.7 | 2098 | 5.5 | 509 |

**Drainage (0 = no, 1 = yes)**

| drainage | time mean % | time n | bins mean % | bins n |
|---|---|---|---|---|
| 0.0 | 3.9 | 3499 | 5.4 | 834 |
| 1.0 | 3.5 | 1377 | 4.8 | 387 |

**Equipment class**

| equipment_class | time mean % | time n | bins mean % | bins n |
|---|---|---|---|---|
| COMBI | 3.8 | 3827 | 5.1 | 986 |
| DUO | 3.7 | 1049 | 5.9 | 235 |

**Container size**

| container_size | time mean % | time n | bins mean % | bins n |
|---|---|---|---|---|
| <=14 m3 | 3.9 | 2623 | 5.0 | 619 |
| 15-20 m3 | 3.7 | 1809 | 5.4 | 539 |
| >20 m3 | 3.7 | 444 | 5.8 | 63 |

**False-full episode in the cycle**

| false_full_in_cycle | time mean % | time n | bins mean % | bins n |
|---|---|---|---|---|
| no | 3.5 | 4294 | 5.0 | 1073 |
| yes | 5.7 | 582 | 6.7 | 148 |

**Weather in the week before lift**

| week | time mean % | time n | bins mean % | bins n |
|---|---|---|---|---|
| dry (bottom 25 %) | 3.7 | 1247 | 5.4 | 309 |
| middle | 3.8 | 2409 | 5.0 | 604 |
| wet (top 25 % rain7) | 4.0 | 1220 | 5.5 | 308 |

## Learning curve (unseen machines, shared model)

| training machines | **overall** | cardboard | paper | plastic_film | mixed_waste |
|---|---|---|---|---|---|
| 10 | 9.7 | 8.3 | 10.5 | 7.7 | 19.0 |
| 25 | 7.6 | 6.6 | 9.8 | 6.2 | 12.3 |
| 50 | 6.7 | 6.3 | 7.1 | 6.6 | 7.7 |
| 100 | 5.9 | 5.8 | 6.0 | 5.4 | 7.6 |
| 240 | 5.4 | 5.1 | 5.2 | 5.0 | 7.6 |

No size below the full 240 training machines gets within 0.5 pp of the all-machines 5.4 %, so by this rule a pilot needs all 240 and the curve has not flattened yet. The largest gain is from 10 to 25 machines (9.7 → 7.6 %).

![learning curve](plots/learning_curve.png)

## Feature importance (top 5 by gain share)

Materials served by the shared model show the same ranking (one model).

| material | split | model | top features |
|---|---|---|---|
| cardboard | time | per_material | p_ewm (50 %), curve_rise_slope_ewm (37 %), pressure_kg_m3 (7 %), presses_cycle (2 %), container_volume_m3 (1 %) |
| mixed_waste | time | per_material | p_ewm (47 %), curve_rise_slope_ewm (42 %), pressure_kg_m3 (6 %), presses_cycle (2 %), fill_ffill (1 %) |
| paper | time | shared | pressure_kg_m3 (84 %), naive_kg_m3 (5 %), curve_rise_slope_ewm (4 %), graph_kg_m3 (2 %), p_ewm (2 %) |
| plastic_film | time | per_material | p_ewm (47 %), curve_rise_slope_ewm (37 %), pressure_kg_m3 (11 %), presses_cycle (2 %), p_last (1 %) |
| cardboard | bins | per_material | p_ewm (52 %), curve_rise_slope_ewm (36 %), pressure_kg_m3 (7 %), presses_cycle (1 %), container_volume_m3 (1 %) |
| mixed_waste | bins | per_material | curve_rise_slope_ewm (65 %), p_ewm (20 %), pressure_kg_m3 (10 %), presses_cycle (2 %), fill_ffill (1 %) |
| paper | bins | per_material | p_ewm (44 %), curve_rise_slope_ewm (23 %), pressure_kg_m3 (23 %), presses_cycle (5 %), naive_kg_m3 (2 %) |
| plastic_film | bins | per_material | p_ewm (45 %), curve_rise_slope_ewm (38 %), pressure_kg_m3 (13 %), presses_cycle (2 %), fill_ffill (1 %) |

![feature importance time](plots/feature_importance_time.png)

![SHAP summary time](plots/shap_time.png)

![feature importance bins](plots/feature_importance_bins.png)

![SHAP summary bins](plots/shap_bins.png)

## Target check (mean % error at lift < 5 %)

| material | split | mean % at lift | n lifts | < 5 % |
|---|---|---|---|---|
| cardboard | time | 4.0 | 2479 | yes |
| paper | time | 4.3 | 702 | yes |
| plastic_film | time | 3.3 | 1168 | yes |
| mixed_waste | time | 3.1 | 527 | yes |
| cardboard | bins | 5.2 | 610 | **no** |
| paper | bins | 5.5 | 184 | **no** |
| plastic_film | bins | 4.6 | 294 | yes |
| mixed_waste | bins | 6.3 | 133 | **no** |

**Not met, and the likely reasons from the data:**

- **cardboard, `bins` split (5.2 %)**: the error is broad-based (median 4.4 %), not driven by a few outliers; 69 of 610 lifts had a false-full episode in the cycle (6.9 % vs 5.0 % without); the fleet has 200 cardboard machine(s) of 400, at most 150 of them in training.
- **paper, `bins` split (5.5 %)**: the error is broad-based (median 4.5 %), not driven by a few outliers; 19 of 184 lifts had a false-full episode in the cycle (7.2 % vs 5.3 % without); the fleet has 60 paper machine(s) of 400, at most 45 of them in training.
- **mixed_waste, `bins` split (6.3 %)**: the error is broad-based (median 6.0 %), not driven by a few outliers; 18 of 133 lifts had a false-full episode in the cycle (7.1 % vs 6.2 % without); the fleet has 40 mixed_waste machine(s) of 400, at most 30 of them in training.
