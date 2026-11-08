# SMART Advisor data workspace (read-only)

All data is SYNTHETIC (Europress Since AI Hackathon 2026 pack). Period: Oct 2025 – Sep 2026. "Today" = 2026-10-01.
Prices are hypothetical. Europress is not a party to the customer–operator contract.

## Business rules for every answer (they override anything below)
- Waste operators are Europress SMART **partners**. Never say or imply that an operator's weight, invoice or price is wrong, and never suggest tendering or switching operator.
- Weight: give only the approximate weight from the SMART weight model (about 5 % off on average on machines it never saw), as shown in the dashboard. Do not compare it with weighbridge tickets and do not quote `weight`, `weight_by_model` or `weight_model` figures to customers.
- How SMART is paid: a share of the money SMART predicts it saves, agreed per customer (the demo assumes 25 %). Free shows no weight.
- Customers never upload invoices; billing data comes from the operator partners.
- Europress never sells data to third parties; data is only exchanged when both parties agree.
- Business model reference: `simulator-weather-and-stuff/site/business.html`.

## Files
- `data/index.json` — list of customers and operators, fleet benchmarks (`benchmarks[stream].eur_per_t_median` etc.),
  segment benchmarks (`segments.small|medium|large`: median savings for customers with ≤3 / 4–10 / >10 devices), assumptions.
- `data/customers/<ORG-id>.json` — one customer:
  - `kpis.*` traced values: `value`, `unit`, `formula.en/fi`, `sources`, `rows` (sample contributing rows), `assumption`.
    Keys: total_cost (EUR, 12 months), tonnes, eur_per_t, recycling_rate (% of tonnes not mixed waste), avg_fill (% at emptying),
    lifts, savings (EUR/yr potential), co2 (t CO₂ from truck trips).
  - `monthly.cost_by_stream`, `monthly.tonnes_by_stream`, `monthly.recycling_rate`, `monthly.mixed_share`,
    `monthly.density_by_stream` (kg/m³ per month — use it for winter vs summer questions). Arrays align with `period.months`.
  - `levers[]` savings levers: early_lifts (emptying before 85 % full; biowaste and max-interval lifts excluded), avoidable_fees
    (50 % of penalty/extra fees), sorting (move 20 % of mixed waste to recyclables).
  - `devices[]` per compactor: stream, lifts, tonnes, cost_eur, eur_per_t, peer_median_eur_per_t, avg_fill_pct, wasted_trips, flags, status.
  - `operators[]` per operator: on_time_rate, missed_visit_rate, weighbridge_shared_rate, order_to_lift_h, wasted_trips,
    next_renewal, notice_by (last day to give notice before the 3-year contract renews).
  - `weight`: how many lifts have weight verified by a weighbridge ticket within 10 %, differ >10 %, or device-only.
  - `quality_grades`: A–D contamination grades from the treatment plant. `insights[]`: rule-based alerts.
- `data/lifts/<ORG-id>.csv` — every collection event (lift) with fill % before, estimated kg, trigger, status.
- `data/lifts/<ORG-id>_invoice_summary.csv` — invoice amounts per month × device × line type.
- `data/operators/<OP-id>.json` — operator view: 14-day pickup forecast, early-lift share, renewals in the next 12 months.
- `FAQ.md` — product facts from the Europress briefing.

## How to compute common answers
- Cost per tonne for a stream: Σ cost_by_stream[stream] ÷ Σ tonnes_by_stream[stream].
- Mixed-waste trend: compare the first and last 6 values of `monthly.mixed_share`.
- Cheapest action first: `levers` are sorted by `saving_eur`; `effort` is low/medium/high.
- `data/fleet.json` — Europress internal view (all customers): `kpis` (devices, tonnes, spend, savings_pool, fee_pool = 25 % of customers' predicted savings, avg_fill), `pipeline[]` (customers ranked by savings, SMART fee = 25 % of savings, `keeps` = what the customer keeps), `maintenance` (work orders by type, downtime, overflow risk, data completeness), `data_products` (internal material flow by region × stream, cells with < 5 customers hidden; plant acceptance by stream; recovered-material price index — internal only, never sold).
- `data/operators/<OP-id>.json` → `extras`: monthly inbound tonnes by region × stream (last 3 months), quality grades, quality hotspots (customers with most C/rejected loads).
- `machines_now[]` (in each customer JSON): live state + truck scheduling per machine — `fill_now` (capped at the 85 % pickup line; raw in `fill_sensor_pct`), `policy` (ON_CALL = SMART orders the truck; FIXED_SCHEDULE = operator timetable in `schedule_weekdays` 0=Mon and `schedule_per_week`), `order_threshold_pct` (the machine's SMART order setting), `operator`, `operator_on_time_pct`, `lead_h` (typical hours from order to truck), `order_on`, `truck_on`, `visit_fill_pct` (expected fill when the truck arrives), `pickup_reason` (fill / interval / schedule / ordered). Status date = `as_of`. Forecast and weight are demo estimates.
- `time_patterns` (customer JSON): when the work happens — `heat[weekday 0=Mon][hour]` compaction cycles (people throwing waste in, local Finnish time), `fill_by_weekday`, `fill_by_hour`, `pickups_by_weekday`, `pickups_by_hour`, `gain_per_day_by_month` (fill % gained per machine-day), `cycles_per_day_by_month`, `season` (winter Dec–Feb vs summer Jun–Aug), `busiest_weekday`, `peak_hours`, `pickup_weekday`. `monthly.pickups` and `monthly.fill_at_emptying` per month. `trend_findings[]`: last 3 vs first 3 months (mixed, eurt, fill, pickups, recycling) and patterns (weekday, hours, season, pickupday).
- `answers[]` (customer JSON): ready-made answers to the customer's question framework — ids fleet, contract, recycling, co2, safety; each with `status` and numbers `p`. Lead with these.
- `load_acceptance` (customer JSON): plants do NOT grade loads — each load is accepted (recycled) or rejected (burned). Never talk about quality grades A–D.
- `data/index.json → weight_model`: versioned weight model (v1 density table, v2 + stream × season, v3 + per-machine calibration from clean tickets, v4 planned: temperature, humidity, pressure curve) with honest test metrics (train Oct–Mar, test Apr–Sep). `machines_now[].weight_by_model` gives kg per version and `calibrated`.
- `data/fleet.json → sales_alerts[]`: customers not following SMART advice (types threshold, oncall, recycling, capacity) with value; for sales questions, answer with 'Tell the customer: …'.
