# compactor_sim — synthetic hourly data for waste compactors

Europress × Since AI Hackathon 2026 (Turku). Generates hourly telemetry and ground
truth for a fleet of press containers (cardboard, paper, plastic film, mixed household waste)
to train a **hydraulic soft sensor**: estimate the mass inside the container every
hour from signals the machine already has (fill, press count, ram pressure) plus
weather, so customers can check the weighbridge figure.

**All data is invented. Nothing represents real Europress data or real Europress
pressure settings.**

The soft-sensor training/prediction pipeline that uses this data lives in
[`weight_train/`](weight_train/README.md); results are in `models/<run>/REPORT.md`.

---

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt

# simulate (400 bins x 365 days ~ 30 s, ~3.5 M hourly rows; 25 bins ~ 8 s)
.venv/bin/python -m compactor_sim --config config.yaml --bins 400 --days 365 --seed 7 --out runs/full400
.venv/bin/python -m compactor_sim --bins 25 --days 365 --out runs/bins25

.venv/bin/python validate.py runs/full400     # sanity report + plots in runs/full400/plots/
.venv/bin/python baseline.py runs/full400     # LightGBM: fill-only vs +pressure vs +rain (~1 min)
.venv/bin/python -m pytest -q                 # 18 tests, ~6 s
```

CLI flags override `run.*` in the config: `--bins`, `--days`, `--seed`, `--out`,
`--no-strokes`. The same seed gives bit-identical output (tested).

### Replaying real FMI weather
Set `weather.mode: fmi_csv` and `weather.fmi_csv.path`. The CSV needs one row per
region and date covering the simulated span (including the 28-day burn-in):

```
region,date,temp_mean_c,precip_mm,rh_pct
UUS,2025-09-03,14.2,0.0,71
```
`rh_pct` is optional (synthesised if absent). Column names can be remapped in
`weather.fmi_csv.columns`. Download FMI daily observations for one station per region
(for example Helsinki-Vantaa, Turku, Tampere-Pirkkala, Oulu, Rovaniemi) and rename
their columns. Hourly disaggregation, snow and snowpack work exactly as in synthetic mode.

---

## Machine behaviour

How the compactor behaves, and how the simulator models it:

| machine behaviour | simulator |
|---|---|
| fill is estimated **only from pressure**, as a rolling average over ~10 cycles, because single strokes spike (a box crushed on the way in) | `sensors.fill_mode: pressure_derived`: 10-stroke exponential rolling average of back pressure → linear fill; per-stroke spikes in `hydraulics.spikes` |
| the ram enters only **20–30 cm** into the container | pressure graph: rise only in the last ~20–35 % of the stroke (`hydraulics.curve.ram_ingress_frac`) |
| back pressure is visible from **5–10 % fill**, then it packs until full | `hydraulics.fill_onset: 0.07`, linear rise |
| fill accuracy about **5 %** | per-installation setpoint calibration → ≈ 6 pp mean error |
| fill tells **volume, not weight** | pressure depends only moderately on pack density (`density_exponent: 1.0`); corr(peak pressure, density) at high fill is weak |
| cardboard and plastic pack densely; **mixed household waste** is heavy and less condensed | material `mixed_waste` (400 kg/m³, compressibility 0.12) replaces mixed plastic |
| the machine can log the **pressure graph** | `pressure_curves.parquet` + hourly `curve_*` features |

---

## Output (one folder per run)

| file | content |
|---|---|
| `hourly.parquet` | one row per machine-hour (bin-major order), columns below |
| `bins.parquet` | machine and site attributes, including hidden truth (`density_factor`, `fill_gain_bias`, `pressure_gain_*`, `relief_bar`, `p_idle_bar`, `daily_input_kg`, …) |
| `weather_daily.parquet` | region, date, temp_mean/min/max_c, precip_mm, rain_mm, snow_mm, rh_pct, snow_swe_mm. **Includes the 28 burn-in days** before `start`, so 7-day lags exist from day 1 |
| `lifts.parquet` | one row per lift: order and lift time, trigger (`fill`/`max_interval`), `false_full_trigger` (bridging active at the order **and** the reading without bridging would have stayed below the threshold), observed/true fill at order, true removed mass split into base / humidity / carried / hopper / free water / ice, `true_moisture_extra`, DUO residue, days since last lift, `weighbridge_net_kg` (±15 kg, 20 kg steps), tare, gross load |
| `strokes.parquet` | one row per ram stroke: ts, oil temp, true and measured peak pressure, duration, energy |
| `pressure_curves.parquet` | raw pressure graphs (16 points along the ram stroke) for the first 5 machines: bin_id, ts, stroke_in_hour, ram_position, pressure_bar |
| `config_used.yaml`, `summary.json` | exact config, plus counts, lift means and the mass-balance ledger |

`hourly.parquet` columns:

| column | type | meaning |
|---|---|---|
| bin_id, ts | str, timestamp[ns, Europe/Helsinki] | machine id, local hour start |
| material, equipment_class, container_volume_m3, region, infeed, drainage | | static attributes |
| rh_pct, temp_c, precip_mm_day | float | day mean RH, hourly air temperature, day total precipitation |
| fill_pct | float | **firmware fill estimate derived from ram pressure** (default `sensors.fill_mode: pressure_derived`): approximate, biased by pack density, 0 below ~30 % fill, held between strokes; plus spurious-high episodes and NaN on dropout. `fill_mode: ultrasonic` gives a direct level sensor instead |
| presses | int | strokes this hour (exact) |
| peak_pressure_bar | float | max stroke peak this hour (NaN if no stroke or dropout) |
| lift_event | int8 | 1 = container emptied at the **start** of this hour (set from the lift itself, also when nothing was lifted) |
| true_mass_kg | float | mass in the machine at hour end (container + press chamber) |
| true_fill_pct, true_density_kg_m3, true_moisture_extra | float | truth |
| oil_temp_c, energy_kwh, precip_mm, snow_mm_day | float | extra signals |
| ref_scale_kg | float | load-cell reading (true × (1 ± 0.5 %)) on ~5 % of machines, else NaN |
| lifted_mass_kg, side_waste_kg | float | truth: mass removed by the lift / left outside this hour |
| true_water_humidity_kg, true_water_carried_kg, true_water_hopper_kg, true_free_water_kg, true_ice_kg | float | water in the container by pathway (humidity can be < 0: drier than reference) |
| true_false_full | bool | a spurious-high episode (jam / bridging) is distorting the fill reading |
| curve_contact_pos, curve_rise_slope_bar, curve_mean_bar, curve_completion | float | **pressure graph** features, hourly mean over strokes: ram position where pressure starts rising (pack face, i.e. geometric fill), rise slope to the peak (density/stiffness), mean pressure over the stroke (∝ work), share of travel before the relief valve opened |

**Lift timing convention:** the lift happens at the start of the `lift_event` hour.
That row's `true_mass_kg` is after the lift. The row one hour earlier holds the mass
that was lifted (`lifted_mass_kg` equals that mass, up to the DUO residue).

---

## Model overview

```
weather (daily synth or FMI) ──► hourly T, rain/snow, RH, snowpack, yard wetness, indoor RH
                                          │
inflow (Poisson events × profiles) ──► base mass per hour ──► water pathways per load
                                          │                     (humidity, carry-in)
                                          ▼
         ┌────────── hourly loop over time, vectorised over all bins ──────────┐
         │ lift due? → env (ice melt, seepage, thaw) → chamber ← load + hopper   │
         │ rain/snow → stroke: chamber → newest layer (or side waste),           │
         │ water → absorbed / drained / pool / ice, pack pressure → layer pmax → │
         │ springback → sensor reading → order policy                            │
         └──────────────────────────────────────────────────────────────────────┘
                                          │
               per-stroke pressure, duration, energy (vectorised after the loop)
```

Code layout (`compactor_sim/`): `config`, `timeline` (DST-safe local clock, Finnish
holidays, truck hours), `fleet`, `weather`, `inflow`, `moisture`, `physics` (pure laws),
`sensors`, `container` (the only stateful part), `operations`, `strokes`, `simulate`,
`output`, `cli`.

Everything that does not depend on container state (weather, inflow, noise, sensor
error, oil temperature) is drawn up front as (hour × bin) arrays. Each random component
has its own seeded stream (`numpy.random.SeedSequence.spawn`). A 28-day burn-in
(random initial fill) desynchronises the fleet before the reported period starts.

### Container physics
* **Layers**: one per loading day since the last lift. Each stores base mass, water by
  pathway, reference volume `vref`, frost volume and the highest pack pressure it
  has experienced, `pmax`.
* **Density law**: `rho = rho_typ · machine_factor · daily_factor · (1 + 0.6·moisture_extra) · g(pmax)`,
  with `g(P) = (1 + c·ln(1 + P/P0)) / (1 + c·ln(1 + P_ref/P0))`. So `rho_typ` is the
  density at the reference pack pressure. Compaction is plastic: a layer only gets
  denser.
* **Springback**: right after a stroke the pack sits `springback` (2–8 %) denser and
  relaxes back over about 3 h, so the fill reading creeps up after pressing.
* **Fill** = Σ layer volume / container volume. Material that does not fit becomes side
  waste. The volume estimate for new material is an upper bound, so true fill never
  exceeds 100 %.
* **Press chamber**: loads in an hour without a stroke (about 15 % of events) wait in
  the chamber and go in with the next stroke. Hopper rain and snow also enter with
  the next stroke.
* **Water routing**: liquid water reaching the pack is soaked up (fibre 30 %, plastics
  10 %). The rest drains (drainage option), seeps slowly (standard container,
  τ = 120 h) or pools (waterproof set-up). Free water adds mass, not volume. Below
  0 °C free water and hopper snow become ice (no drainage) and melt later at
  2 %/h/°C. Wet loads entering below 0 °C form frozen lumps (+8–15 % volume) that
  thaw over about 12 h.
* **Lift**: COMBI empties fully. DUO keeps a residue of 0–2 % of the layers. The final
  press before the swap also takes the chamber contents.
* **Mass balance** (per bin, reported period): `start + material_in + hopper_precip − drained − seeped − side_waste − lifted = end`.
  Closes to about 1e-9 kg (tested).

### Hydraulics
`P_peak = min(relief, visc(T_oil) · (P_idle + k · f(fill) · stiffness · (rho/rho_typ)^1.0 · softening(moisture) · (1 + 1.5·frost_share)))`
with `f(fill) = clip((fill − 0.07)/0.93, 0, 1)` (back pressure from ~7 % fill, rising linearly) and density exponent 1.0. The pack only receives the part of
the pressure left after viscous losses (`relief/visc − P_idle`), so density growth
stalls when the valve opens. Wet fibre is softer (softening 1 − 2.5·moisture): a wet
pack is heavier at the same pressure, which is why weather adds information beyond
pressure. Per stroke, oil warms by 0.8 °C, so the first morning strokes are cold and
read high. The transducer adds 4 bar noise, a gain drift of about 2 %/yr and 0.5 bar
quantisation.

---

## Assumptions (value — source)

"brief" = the hackathon task specification (machine data there cites europressgroup.com).
"assumed" = my choice, not validated against real data. All are in `config.yaml`.

**Fleet**
| item | value | source |
|---|---|---|
| materials | cardboard 50 %, paper 15 %, film 25 %, mixed waste 10 % (exact counts by largest remainder). Mixed waste (household incl. bio waste) replaces the brief's mixed plastic | brief / machine behaviour |
| COMBI / DUO volumes | 10–22 m³ / 20–35 m³ | brief |
| volume mix per material | `fleet.containers`; paper on 10–14 m³, mixed waste on 10/14 m³ COMBI and 20 m³ DUO (heavy: keeps lifts < 10 t) | assumed (to hit lift-mass targets) |
| DUO tare | 2.3–3.0 t, linear in volume | brief |
| COMBI unit tare | 4.0–6.5 t | assumed |
| press models | 25 t (Combi/Duo, 42 s), 35 t (66 s), 40 t (35 s, 7.5 kW); aperture 1.85×1.30 m (Max) / 1.50×0.90 m | brief |
| 25 t cycle for Combi, motor 4 / 5.5 kW | 42 s, 4 / 5.5 kW | assumed |
| infeed | open hopper 45 %, lidded 25 %, indoor shaft 30 %; drainage 30 % | brief |
| waterproof share (of undrained) | 25 % | assumed |
| indoor installation (of shafts) | 40 %, 12 °C | assumed |
| floating roof | 50 % of cardboard DUO ≥ 24 m³, +10 % density | assumed |
| regions | UUS 35 %, VAR 18 %, PIR 20 %, POP 15 %, LAP 12 % | assumed |
| sector by material | `fleet.sector_by_material` | assumed |
| order threshold | 80 / 85 / 90 % (30/40/30) | brief / assumed shares |
| days to threshold | lognormal, median 8 d (σ_log 0.35), clipped 3–10 | brief range, assumed shape and median: with the ~1 d order-to-lift lag (truck 4–30 h) a shorter cycle makes overflow the normal case rather than the exception |
| storage share outdoors | Beta per infeed (open 0.6, lidded 0.5, shaft 0.14 mean) | assumed |
| reference scale | 5 % of machines, 0.5 % noise | brief |

**Sites and inflow**
| item | value | source |
|---|---|---|
| opening hours | retail 7–21 (Sat 8–20, Sun 12–18); warehouse 6–22 (Sat 7–15); industry 6–22 Mon–Fri; hotel 6–23 daily; ±1 h per site | assumed |
| monthly profile | retail Dec ×1.35; industry Jul ×0.35; hotel Jun–Aug ×1.25–1.45 | brief (shape), assumed (other months) |
| holidays | Finnish public holidays (incl. Easter, Ascension, Midsummer, All Saints); closed except hotels | brief |
| event mass | gamma, shape 2; mean 18 / 22 / 9 / 12 kg (cardboard / paper / film / mixed waste) | brief means (mixed waste assumed), assumed shape |
| day-to-day noise | AR(1) lognormal, φ 0.6, σ 0.30 | assumed |
| bulk days | 2 % of open days, ×2–4 | brief (×2–4), assumed rate |
| press per event | 85 %; test cycles 0.004 per open hour | brief / assumed |

**Weather**
| item | value | source |
|---|---|---|
| temperature | Jan/Jul means UUS −3.5/17.5, VAR −4/17.5, PIR −6.5/17, POP −9.5/16.5, LAP −13/14.5 °C; sine minimum 20 Jan | approximates FMI 1991–2020 normals; brief anchors |
| anomaly | AR(1) φ 0.8, sd 5 °C winter / 2.5 °C summer, 75 % shared between regions | assumed |
| diurnal | amplitude 1.5 (winter) – 4.5 °C (summer), max 15:00 | assumed |
| precipitation | annual 660/670/620/520/490 mm; wet-day (≥0.1 mm) prob ~0.48; persistence p11−p01 = 0.35; gamma shape 0.75; seasonal peak early Sep (amount ±30 %) | brief targets; assumed parameters |
| hourly rain | block of 2 + 1.5√mm + Exp(1) hours, Dirichlet(0.7) weights | assumed |
| snow | precipitation at T < 0 °C; snowpack degree-day 3 mm/°C/day | brief / assumed |
| RH | mean 79–82 %, seasonal ±8–10 % (peak mid-Dec), +6 % on wet days, AR(1) sd 4 %; diurnal 3–14 % | assumed (Finnish climate order of magnitude) |
| indoor RH | outdoor vapour pressure at 21 °C + 8 %, 15–65 % | assumed (physics) |

**Moisture**
| item | value | source |
|---|---|---|
| fibre EMC | Oswin `MC = 5.8·(h/(1−h))^0.30` % → 4.5 % @30 %, 7 % @65 %, 14 % @95 % RH | fitted to brief anchors |
| reference moisture | 7 % (event masses refer to this) | assumed |
| storage equilibrium | 24 h exponential memory | brief ("within about a day") |
| carry-in | wet yard: fibre 4 % / 3.5 % of mass, plastics 1 % | brief magnitudes |
| hopper ingress | rain × aperture area; lidded 10 %, shaft 0 | brief |
| soak-up | fibre 30 %, plastics 10 % of free water | brief |
| wet density | ×(1 + 0.6·moisture_extra), fibre only | brief |
| frost | lumps +15 % (cardboard) / 12 % / 8 % volume, thaw τ 12 h; ice melt 2 %/h/°C | assumed |
| container temperature | 12 h EMA of air | assumed |

**Compaction / hydraulics / sensors / operations**
| item | value | source |
|---|---|---|
| rho_typ | 190 / 330 / 140 / 400 kg/m³ (cardboard / paper / film / mixed waste) at P_ref 120 bar | brief; mixed waste assumed (heavy) |
| compressibility c | 0.25 / 0.15 / 0.35 / 0.12, P0 20 bar | brief c; mixed waste assumed (compacts less) |
| springback | 4 / 2 / 8 / 6 %, τ 3 h | assumed |
| machine density factor | lognormal cv 15 % × (force/30 t)^0.15 | brief cv, assumed force law |
| daily density factor | cv 7 % | brief |
| P_idle, k, relief | 20–30 bar, k 150 bar (cv 5 %), relief 240 bar (cv 2 %); back pressure from 7 % fill, linear in fill (`fill_onset` 0.07, exponent 1.0); density exponent 1.0 (medium: pressure mostly tracks volume); crushed-item spikes +20–80 bar (6 % of cardboard strokes, 1–3 % others) | brief ranges; onset, linearity, spikes from machine behaviour; values assumed (**real values unknown**) |
| viscosity | +0.4 %/°C below 40 °C | brief |
| oil | +0.8 °C/stroke, cools to ambient with τ 2 h, max 60 °C | assumed |
| stroke | duration ×(1 + 0.35·load²), +0.3 %/°C below 20 °C; energy = motor kW × duration × (0.25 + 0.65·load)/0.88 | assumed |
| fill reading (default) | firmware: exponential rolling average over ~10 strokes of the back pressure (peak − idle), mapped linearly to fill; reads 100 % at a 'full' back-pressure setpoint (150 bar × material stiffness) calibrated per installation at commissioning; no oil-temperature correction (cold oil reads fuller). Mean error ≈ 6 pp (≈ 5.5 pp above 30 % fill) | machine behaviour (pressure only, rolling ~10 cycles, ~5 % accuracy, reads full at a set back pressure); calibration assumed |
| fill reading (`ultrasonic` mode) | noise 3 %, gain bias ±3 %, drift 0.1 %/day RW, integer % | brief / assumed drift |
| pressure graph | 16 samples per stroke. The ram enters only ~20–30 cm into the container, so the rise starts at x_c = 0.80 − 0.15·clip((fill − 0.07)/0.93) ± 0.03; P(x) = min(relief, visc·(idle + excess·ramp^q + loose) + spike bump), q = 1.2 + 2·(0.35 − c); device detects contact at +15 bar over the start | machine behaviour (ram travel, graph available); shape assumed |
| false full / spurious high | 1 per 60 days, 1–4 days at 88–97 %, only once ≥ 15 % true fill, clears at lift (with the pressure-derived reading: e.g. a jammed bulky item) | brief / assumed last two |
| pressure sensor | 4 bar noise, 0.5 bar steps, gain bias 1 %, drift 2 %/yr (sd) | brief |
| dropouts | 1 % of hours + outages 1 per 50 days, lognormal median 6 h (≤ 96 h) | brief / assumed outages |
| collection | order at observed fill ≥ threshold, max interval 28 d (ordered 30 h ahead); truck 4–30 h later, 06–18, no Sundays or holidays | brief |
| DUO residue | 0–2 % | brief |

---

## Validation results (seed 7, 400 bins, 365 days; `validate.py`)

Current physics: pressure-derived fill, medium density link, mixed waste.

| check | result | target |
|---|---|---|
| mass balance residual | 2.3e-9 kg max | 0 |
| lift mass cardboard / paper / film / mixed waste | 3.04 / 3.39 / 1.84 / 4.86 t | 2–3.5 / 2.5–4 / 1.2–2.2 / 3–6 t (mixed waste assumed) |
| heaviest net lift | **11.7 t** (34 of 20,281 lifts > 10 t, all mixed waste in 20 m³ DUO) | < ~10 t |
| days between lifts | median 7.0, 98 % within 2–14 d | mostly 2–14 |
| overflow | side waste 2.6 % of inflow; 18 % of lifts with some side waste | exception, not the rule |
| fill reading (pressure-derived) | corr 0.95 with true fill; ≈ 6 pp mean error | ~5 % (reference) |
| pressure graph | corr(contact point, true fill) −0.34 (ram enters only 20–30 cm) | — |
| annual precipitation UUS/VAR/PIR/POP/LAP | 724 / 723 / 601 / 526 / **359** mm (30-seed mean 659/676/625/521/507) | 600–700 S, 450–550 N |
| Jan / Jul mean T | UUS −3.9 / 18.4 °C, LAP −12.7 / 15.1 °C | −4 / 17 S, −12 / 15 N |
| cardboard lift mass, wet+humid vs dry week | open hopper +4.4 %, lidded +2.9 %, indoor shaft −1.4 % | a few %, open > shaft |
| peak pressure at true fill ≥ 85 % | p10 140, p50 182, p90 234 bar | typical 150–220, cap ~240 |
| corr(peak pressure, density) at fill ≥ 85 % | 0.73 (cardboard) – 0.87 (film), across machines | weaker than before (medium link) |
| false-full-triggered lifts | 10.2 % (mean true fill 52 %, 1.68 t vs 3.13 t) | — |

Outside target:
* **Heaviest lift 11.7 t**: mixed waste at 400 kg/m³ in a 20 m³ DUO occasionally exceeds a
  hook-lift truck's ~10 t. Restricting mixed waste to ≤ 14 m³ containers (or ≤ 17 m³) would fix it.
* **Precipitation for seed 7**: one synthetic year with realistic interannual variability; the
  generator is unbiased over seeds.

## Baseline soft sensor (`baseline.py`)

Quick LightGBM check (5-fold CV grouped by machine, lifts ≥ 200 kg). The full pipeline with both
splits, intervals and the pressure graph is `weight_train/` (results in `models/full400/REPORT.md`).

| features | mean % err | median | p90 | WAPE |
|---|---|---|---|---|
| fill only | 23.3 | 15.1 | 40.2 | 19.0 |
| + pressure | 7.2 | 5.8 | 14.6 | 7.1 |
| + pressure + presses | 5.2 | 4.1 | 10.8 | 5.2 |
| + rain + RH (7 d) | 5.2 | 4.1 | 10.7 | 5.1 |

With the fill reading itself derived from pressure, fill alone is a weak predictor of weight
(23 %). Peak pressure and the number of presses since the last lift carry most of the information;
weather adds almost nothing measurable.

## Limitations
* Weather is regional (5 points). There is no microclimate, wind-driven rain, roof
  overhangs or hopper canopy.
* The pressure law is phenomenological and **its parameters are not real Europress
  values**. There is no position or stroke-length signal, no partial strokes, and no
  pack friction along the container.
* All layers share the pack pressure of each stroke. There is no pressure gradient
  front-to-back and no material creep beyond springback.
* Each machine is its own site. Co-located machines and shared staff behaviour are not
  modelled. Side waste leaves the system instead of being reloaded after the lift.
* Hygroscopic moisture is fixed at loading. The closed container does not exchange
  moisture afterwards, and pooled water does not soak into the bottom layers.
* Truck logistics are simple (independent random delay, no route capacity). Lifts during
  telemetry outages still happen. Orders use only valid readings.
* Overflow is reduced but not gone. Fast sites still overfill before the truck arrives:
  side waste is 2.6 % of inflow, 18 % of lifts had some side waste since the previous
  lift, and 13 % of lifts found the container at ≥ 99 % true fill.
* The fill ceiling differs slightly by material. Right after a stroke the pack is
  compressed by the springback and relaxes over about 3 h, so the reported true fill of a
  completely full container is about 100·(1 − 0.85·springback): ~96.6 % cardboard,
  98.3 % paper, 93.2 % film, 98.3 % mixed waste.
* With the defaults (3–10 days to threshold) no site is slow enough to hit the 28-day
  max interval. Raise `target_cycle_days.max` (for example to 30) to get scheduled
  lifts of part-full containers.
