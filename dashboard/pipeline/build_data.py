"""Build dashboard + AI-workspace JSON from the Europress SMART synthetic competition pack.

Every metric is emitted as a "traced value": value + unit + formula (en/fi) + source tables
+ sample contributing rows, so the UI can show "where does this number come from".

Run:  python -I pipeline/build_data.py
"""
from __future__ import annotations

import json
import os
import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
# Since AI competition pack (not shipped); point EUROPRESS_PACK at its data/competition/public folder.
PACK = Path(os.environ.get(
    "EUROPRESS_PACK",
    "Europress_SMART_Material_Value_Engine_v1.0.1/data/competition/public",
))
OUT = ROOT / "public" / "data"
AI = ROOT / "ai-workspace"

TARGET_FILL_PCT = 85.0            # SMART optimisation target fill at emptying [assumption]
REJECTED_ALERT_SHARE = 0.03      # flag when > 3 % of loads are rejected at the plant (burned) [assumption]
AUTO_PICKUP_FROM_PCT = 80.0      # at/above this SMART has already ordered the pickup [assumption]
LOW_FILL_PCT = 60.0               # lifts below this are "early lifts"
RECYCLABLE_SHARE_IN_MIXED = 0.20  # share of mixed waste that could be sorted [assumption]
CO2_KG_PER_KM = 1.1               # heavy refuse truck, diesel [assumption]
AVOIDABLE_FEE_SHARE = 0.5         # share of penalty/extra fees avoidable [assumption]
CONTRACT_YEARS = 3               # typical customer-operator contract (Europress, briefing 24:40)
TODAY = pd.Timestamp("2026-10-01")
DATA_AS_OF = TODAY - pd.Timedelta(days=1)   # last day with data; every date-relative claim is "as of" this day
NOTICE_SOON_MONTHS = 6            # a notice deadline within this many months of TODAY is flagged "soon"
NOTICE_SOON_BEFORE = TODAY + pd.DateOffset(months=NOTICE_SOON_MONTHS)
RECYCLED = {"cardboard", "paper", "plastic_film", "mixed_plastic", "biowaste"}
FIXED_LIFT_LINES = {"LIFT_FEE", "TRANSPORT", "FUEL_SURCHARGE", "REMOTE_SURCHARGE"}
AVOIDABLE_FEE_LINES = {
    "WASTED_TRIP_FEE", "CANCELLATION_FEE", "EMERGENCY_SURCHARGE",
    "CONTAMINATION_PENALTY", "SIDE_WASTE_FEE", "REJECTION_REPROCESSING", "MIN_CHARGE_TOPUP",
}

SRC = {
    "events": "operations/collection_events.parquet",
    "tickets": "operations/weighbridge_tickets.parquet",
    "quality": "operations/quality_assessments.parquet",
    "invoices": "finance/invoices.parquet",
    "lines": "finance/invoice_lines.parquet",
    "devices": "reference/devices.parquet",
    "contracts": "reference/contracts.parquet",
    "versions": "reference/contract_versions.parquet",
    "sites": "reference/sites.parquet",
    "telemetry": "telemetry/telemetry_daily_summary.parquet",
    "scorecard": "labels_dev/operator_scorecard.parquet",
    "refcharges": "labels_dev/reference_charge_lines.parquet",
}

LIFT_COLS = ["collection_id", "device_id", "material_stream", "actual_at", "device_fill_pct_before",
             "device_estimated_collected_kg", "weight_reconciliation_status"]
LINE_COLS = ["invoice_line_id", "billing_period", "device_id", "material_stream", "line_type",
             "quantity", "unit", "unit_price_eur", "amount_eur"]


def load(key: str) -> pd.DataFrame:
    return pd.read_parquet(PACK / SRC[key])


def r(x, n=1):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, n) if n else int(round(x))


def sample(df: pd.DataFrame, cols: list[str], n: int = 12) -> dict:
    d = df[cols].head(n).copy()
    for c in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[c]):
            d[c] = d[c].dt.strftime("%Y-%m-%d %H:%M")
        elif pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].round(2)
    d = d.astype(object).where(pd.notna(d), None)
    return {"columns": cols, "rows": d.values.tolist(), "total_rows": int(len(df))}


def traced(value, unit, formula_en, formula_fi, sources, rows=None, assumption=None, n=1):
    return {
        "value": r(value, n),
        "unit": unit,
        "formula": {"en": formula_en, "fi": formula_fi},
        "sources": [SRC.get(s, s) for s in sources],
        "rows": rows,
        "assumption": assumption,
    }


def roll_renewals(con: pd.DataFrame) -> pd.DataFrame:
    """Next renewal date = contract_start + k * CONTRACT_YEARS, first one after TODAY."""
    con = con.copy()
    start = pd.to_datetime(con.contract_start)
    years = np.ceil(((TODAY - start).dt.days / 365.25) / CONTRACT_YEARS).clip(lower=1) * CONTRACT_YEARS
    con["renewal"] = [s + pd.DateOffset(years=int(y)) for s, y in zip(start, years)]
    con["notice_by"] = con.renewal - pd.to_timedelta(con.notice_period_days, unit="D")
    return con


def as_of_fields() -> dict:
    """The date the data stands at, and the 'notice soon' cut-off the UI uses (never the browser clock)."""
    return {"as_of": str(DATA_AS_OF.date()), "notice_soon_before": str(NOTICE_SOON_BEFORE.date())}


# ----------------------------------------------------------------------------- load
def prepare() -> dict:
    dev = load("devices")
    ev = load("events")
    ev["month"] = ev["actual_at"].dt.strftime("%Y-%m")
    ev = ev.merge(dev[["device_id", "material_stream", "container_volume_m3"]], on="device_id", how="left")
    inv = load("invoices")
    lines = load("lines").merge(
        inv[["invoice_id", "contract_id", "customer_id", "operator_id", "billing_period"]], on="invoice_id")
    lines = lines.merge(dev[["contract_id", "device_id", "material_stream", "site_id"]], on="contract_id", how="left")
    tk = load("tickets").merge(ev[["operator_job_ref", "collection_id", "device_id", "customer_id"]],
                               on="operator_job_ref", how="left")
    qa = load("quality").merge(tk[["ticket_id", "customer_id", "device_id"]], on="ticket_id", how="left")
    return dict(
        dev=dev, ev=ev, inv=inv, lines=lines, tk=tk, qa=qa, tel=load("telemetry"), con=load("contracts"),
        hourly=hourly_activity(),
        sc=load("scorecard"), ref=load("refcharges"), sites=load("sites"), ver=load("versions"),
        cust=pd.read_parquet(PACK / "reference/customers.parquet"),
        ops=pd.read_parquet(PACK / "reference/operators.parquet"),
    )


# ----------------------------------------------------------------------------- fleet benchmarks
def fleet_benchmarks(D):
    lifts = D["ev"][D["ev"].status.isin(["COMPLETED", "PARTIAL"])]
    kg = lifts.groupby("device_id").device_estimated_collected_kg.sum()
    cost = D["lines"].groupby("device_id").amount_eur.sum()
    dd = D["dev"].set_index("device_id")[["material_stream", "customer_id"]].join(kg.rename("kg")).join(cost.rename("eur"))
    dd = dd[dd.kg > 0]
    dd["eur_t"] = dd.eur / (dd.kg / 1000)
    by_stream = dd.groupby("material_stream").eur_t.describe(percentiles=[.25, .5, .75])
    fill = lifts.groupby("material_stream").device_fill_pct_before.median()
    out = {}
    for s, row in by_stream.iterrows():
        out[s] = {"eur_per_t_p25": r(row["25%"]), "eur_per_t_median": r(row["50%"]), "eur_per_t_p75": r(row["75%"]),
                  "median_fill_at_pickup_pct": r(fill.get(s)), "n_devices": int(row["count"])}
    return out, dd


# ----------------------------------------------------------------------------- customer: devices
def device_rows(D, cid, bench):
    dev = D["dev"][D["dev"].customer_id == cid]
    ev = D["ev"][D["ev"].customer_id == cid]
    lifts = ev[ev.status.isin(["COMPLETED", "PARTIAL"])]
    lines = D["lines"][D["lines"].customer_id == cid]
    sites = D["sites"].set_index("site_id")
    rows = []
    for _, d in dev.iterrows():
        dl = lifts[lifts.device_id == d.device_id]
        kg = dl.device_estimated_collected_kg.sum()
        eur = lines[lines.device_id == d.device_id].amount_eur.sum()
        eur_t = eur / (kg / 1000) if kg > 0 else None
        fill = dl.device_fill_pct_before.mean()
        b = bench.get(d.material_stream, {})
        flags = []
        if fill == fill and fill < 70 and d.material_stream != "biowaste":
            flags.append("early_lifts")
        if eur_t and b.get("eur_per_t_p75") and eur_t > b["eur_per_t_p75"]:
            flags.append("high_eur_t")
        wasted = int((ev[ev.device_id == d.device_id].status == "WASTED_TRIP").sum())
        if wasted >= 2:
            flags.append("wasted_trips")
        status = "good" if not flags else ("action" if len(flags) >= 2 or "high_eur_t" in flags else "review")
        rows.append({
            "device_id": d.device_id, "site": sites.loc[d.site_id, "site_name"].split(" (")[0],
            "stream": d.material_stream, "volume_m3": r(d.container_volume_m3), "policy": d.collection_policy,
            "operator_id": d.operator_id, "lifts": int(len(dl)), "tonnes": r(kg / 1000, 2), "cost_eur": r(eur, 0),
            "eur_per_t": r(eur_t, 0), "peer_median_eur_per_t": b.get("eur_per_t_median"),
            "avg_fill_pct": r(fill, 0), "wasted_trips": wasted, "flags": flags, "status": status,
        })
    return rows


# ----------------------------------------------------------------------------- customer: levers
def lever_early_lifts(lifts, lines):
    # hygiene-bound streams and lifts forced by the max-interval rule are not avoidable
    lifts = lifts[(lifts.material_stream != "biowaste") & (lifts.trigger_type != "MAX_INTERVAL_RULE")]
    fixed = lines[lines.line_type.isin(FIXED_LIFT_LINES)].groupby("device_id").amount_eur.sum()
    nlifts = lifts.groupby("device_id").size()
    fillsum = lifts.groupby("device_id").device_fill_pct_before.sum()
    per_lift = (fixed / nlifts).dropna()
    needed = np.ceil(fillsum / TARGET_FILL_PCT)
    avoid = (nlifts - needed).clip(lower=0)
    tbl = pd.DataFrame({"lifts": nlifts, "sum_fill_pct": fillsum.round(0), "lifts_needed_at_85pct": needed,
                        "avoidable_lifts": avoid, "fixed_cost_per_lift_eur": per_lift.round(2)}).dropna()
    tbl["saving_eur"] = (tbl.avoidable_lifts * tbl.fixed_cost_per_lift_eur).round(0)
    tbl = tbl.reset_index().sort_values("saving_eur", ascending=False)
    saving = float(tbl.saving_eur.sum())
    lever = {"id": "early_lifts", "saving_eur": r(saving, 0), "effort": "low",
             "trace": traced(saving, "EUR/yr",
                             f"Σ devices (lifts − ceil(Σ fill% at pickup ÷ {TARGET_FILL_PCT:.0f}%)) × (lift fee + transport + fuel + remote surcharge per lift)",
                             f"Σ laitteet (tyhjennykset − ylöspäin(Σ täyttöaste ÷ {TARGET_FILL_PCT:.0f} %)) × (tyhjennysmaksu + kuljetus + polttoaine + syrjäseutulisä / tyhjennys)",
                             ["events", "lines"], sample(tbl, tbl.columns.tolist()),
                             f"Target fill at emptying {TARGET_FILL_PCT:.0f}% (SMART optimisation target)", 0)}
    return lever, avoid, tbl.set_index("device_id").saving_eur


def lever_fees(lines):
    fees = lines[lines.line_type.isin(AVOIDABLE_FEE_LINES)]
    total = fees.amount_eur.sum() * AVOIDABLE_FEE_SHARE
    return {"id": "avoidable_fees", "saving_eur": r(total, 0), "effort": "medium",
            "breakdown": {k: r(v, 0) for k, v in fees.groupby("line_type").amount_eur.sum().items()},
            "trace": traced(total, "EUR/yr",
                            f"Σ invoice lines of type wasted trip, cancellation, emergency, contamination, side waste, rejection, minimum-charge top-up × {AVOIDABLE_FEE_SHARE:.0%}",
                            f"Σ laskurivit: turha käynti, peruutus, kiireellinen, kontaminaatio, sivujäte, hylkäys, minimiveloitus × {AVOIDABLE_FEE_SHARE:.0%}",
                            ["lines", "invoices"], sample(fees.sort_values("amount_eur", ascending=False), LINE_COLS),
                            f"{AVOIDABLE_FEE_SHARE:.0%} of these fees can be avoided with alerts, sorting guidance and earlier ordering", 0)}


def lever_sorting(cid, lines, t_m, fleet_dd, bench):
    mixed_cost = lines[lines.material_stream == "mixed_waste"].amount_eur.sum()
    mixed_t = float(t_m["mixed_waste"].sum()) if "mixed_waste" in t_m else 0.0
    rec = fleet_dd[(fleet_dd.customer_id == cid) & fleet_dd.material_stream.isin(["cardboard", "plastic_film", "paper"])]
    alt = rec.eur.sum() / (rec.kg.sum() / 1000) if rec.kg.sum() > 0 else (bench.get("cardboard", {}).get("eur_per_t_median") or 0)
    mixed_eur_t = mixed_cost / mixed_t if mixed_t else 0
    saving = max(0.0, (mixed_eur_t - alt) * mixed_t * RECYCLABLE_SHARE_IN_MIXED) if mixed_t else 0.0
    tbl = pd.DataFrame([{"mixed_waste_t": r(mixed_t, 1), "mixed_cost_eur": r(mixed_cost, 0), "mixed_eur_per_t": r(mixed_eur_t, 0),
                         "recyclable_stream_eur_per_t": r(alt, 0), "share_moved": RECYCLABLE_SHARE_IN_MIXED, "saving_eur": r(saving, 0)}])
    return {"id": "sorting", "saving_eur": r(saving, 0), "effort": "medium",
            "trace": traced(saving, "EUR/yr",
                            f"(mixed waste €/t − recyclable streams €/t) × mixed tonnes × {RECYCLABLE_SHARE_IN_MIXED:.0%} moved to sorting",
                            f"(sekajätteen €/t − kierrätysjakeiden €/t) × sekajätetonnit × {RECYCLABLE_SHARE_IN_MIXED:.0%} lajitteluun",
                            ["lines", "events", "devices"], sample(tbl, tbl.columns.tolist()),
                            f"{RECYCLABLE_SHARE_IN_MIXED:.0%} of mixed waste is sortable cardboard/plastic (typical audit finding; to be validated)", 0)}


def lever_tender(cid, fleet_dd, bench, early_by_device):
    """Price gap left AFTER fuller pickups — avoids double counting the early-lift saving."""
    dd = fleet_dd[fleet_dd.customer_id == cid].copy()
    dd["early_saving"] = early_by_device.reindex(dd.index).fillna(0)
    dd["eur_after_fill"] = dd.eur - dd.early_saving
    dd["eur_t_after_fill"] = dd.eur_after_fill / (dd.kg / 1000)
    dd["peer_median"] = dd.material_stream.map(lambda s: bench.get(s, {}).get("eur_per_t_median"))
    dd["gap_eur"] = (dd.eur_t_after_fill - dd.peer_median).clip(lower=0) * dd.kg / 1000 * 0.5
    saving = float(dd.gap_eur.sum())
    tbl = dd.reset_index()[["device_id", "material_stream", "kg", "eur", "early_saving", "eur_t_after_fill", "peer_median", "gap_eur"]].round(1)
    tbl = tbl.sort_values("gap_eur", ascending=False)
    return {"id": "tender", "saving_eur": r(saving, 0), "effort": "high",
            "trace": traced(saving, "EUR/yr",
                            "Σ devices max(0, €/t after fuller pickups − fleet median €/t for the same stream) × tonnes × 50% (closing half the remaining gap at renegotiation/tender)",
                            "Σ laitteet max(0, €/t täydempien tyhjennysten jälkeen − saman jakeen mediaani €/t) × tonnit × 50 % (puolet jäljelle jäävästä erosta suljetaan neuvottelussa/kilpailutuksessa)",
                            ["lines", "events", "devices"], sample(tbl, tbl.columns.tolist()),
                            "Half of the gap to the fleet median can be closed at the next tender", 0)}


def lever_invoice_check(D, ev, lines):
    ref = D["ref"].groupby("collection_id").amount_eur.sum()
    lj = lines.dropna(subset=["operator_job_ref"]).merge(ev[["operator_job_ref", "collection_id"]], on="operator_job_ref")
    billed = lj.groupby("collection_id").amount_eur.sum()
    cmp_ = pd.DataFrame({"invoiced_eur": billed, "expected_per_contract_eur": ref}).dropna()
    cmp_["difference_eur"] = (cmp_.invoiced_eur - cmp_.expected_per_contract_eur).round(2)
    diffs = cmp_[cmp_.difference_eur.abs() > 5].sort_values("difference_eur", ascending=False).reset_index()
    over = float(diffs[diffs.difference_eur > 0].difference_eur.sum())
    return {"id": "invoice_check", "saving_eur": r(over, 0), "effort": "low", "count": int((diffs.difference_eur > 0).sum()),
            "trace": traced(over, "EUR/yr",
                            "Σ lifts where invoiced amount − amount recomputed from the contract price list > €5 (job reference match)",
                            "Σ tyhjennykset, joissa laskutettu − sopimushinnastosta laskettu > 5 € (työnumerolla yhdistetty)",
                            ["lines", "events", "versions", "refcharges"], sample(diffs, diffs.columns.tolist()),
                            "Differences are 'needs a second look', not errors: side waste, tare and combined loads explain many", 0)}


# ----------------------------------------------------------------------------- customer: operators
def customer_operators(D, cid, ev):
    con = roll_renewals(D["con"][D["con"].customer_id == cid])
    sc = D["sc"]
    opsref = D["ops"].set_index("operator_id")
    out = []
    for op, g in con.groupby("operator_id"):
        s = sc[sc.operator_id == op]
        opev = ev[ev.operator_id == op]
        out.append({
            "operator_id": op, "name": opsref.loc[op, "operator_name"], "contracts": int(len(g)),
            "on_time_rate": r(s.on_time_rate.mean() * 100), "missed_visit_rate": r(s.missed_visit_rate.mean() * 100, 2),
            "weighbridge_shared_rate": r(s.weighbridge_shared_7d_rate.mean() * 100),
            "order_to_lift_h": r(s.median_order_to_lift_hours.median()),
            "wasted_trips": int((opev.status == "WASTED_TRIP").sum()), "missed": int((opev.status == "MISSED_BY_OPERATOR").sum()),
            "next_renewal": g.renewal.min().strftime("%Y-%m-%d"), "notice_by": g.notice_by.min().strftime("%Y-%m-%d"),
            "trace": traced(s.on_time_rate.mean() * 100, "%", "mean monthly on-time rate across regions (operator scorecard)",
                            "kuukausittainen ajallaan-osuus, alueiden keskiarvo (operaattorin tuloskortti)", ["scorecard", "events"],
                            sample(s, ["operator_id", "region_code", "month", "on_time_rate", "missed_visit_rate",
                                       "weighbridge_shared_7d_rate", "median_order_to_lift_hours"])),
        })
    return out


def build_insights(h1, h2, grades, ops, early_saving, avoid_km):
    ins = []
    if h1 == h1 and h2 == h2:
        if h2 - h1 > 2:
            ins.append({"id": "mixed_rising", "params": {"from": r(h1), "to": r(h2)}, "severity": "warn"})
        elif h1 - h2 > 2:
            ins.append({"id": "mixed_falling", "params": {"from": r(h1), "to": r(h2)}, "severity": "good"})
    n_qa = sum(grades.values())
    bad = grades.get("rejected", 0)      # plants do not grade: a load is accepted, or rejected and burned
    if n_qa and bad > REJECTED_ALERT_SHARE * n_qa:
        ins.append({"id": "contamination", "params": {"share": r(bad / n_qa * 100)}, "severity": "warn"})
    for o in ops:
        if pd.Timestamp(o["notice_by"]) < NOTICE_SOON_BEFORE:
            ins.append({"id": "tender_window", "params": {"operator": o["name"].replace(" (fictional)", ""), "date": o["notice_by"]}, "severity": "info"})
    if early_saving > 200:
        ins.append({"id": "early_lifts", "params": {"eur": r(early_saving, 0), "km": r(avoid_km, 0)}, "severity": "warn"})
    return ins


PRICE_TERMS = [  # contract_versions column → (key, unit) ; cents → EUR, bp → %
    ("lift_fee_cents", "lift_fee", "EUR/lift"), ("per_km_cents", "per_km", "EUR/km"), ("free_km", "free_km", "km"),
    ("haul_fee_cents_per_t", "haul_fee", "EUR/t"), ("processing_fee_cents_per_t", "processing_fee", "EUR/t"),
    ("credit_share_bp", "credit_share", "%"), ("rental_cents_per_month", "rental", "EUR/month"),
    ("min_charge_cents", "min_charge", "EUR"), ("wasted_trip_fee_cents", "wasted_trip_fee", "EUR"),
    ("side_waste_fee_cents", "side_waste_fee", "EUR"), ("emergency_surcharge_cents", "emergency_surcharge", "EUR"),
    ("contamination_penalty_cents", "contamination_penalty", "EUR"), ("cancellation_fee_cents", "cancellation_fee", "EUR"),
    ("fuel_surcharge_bp", "fuel_surcharge", "%"),
]


def customer_account(D, cid, dev):
    """Account page: sites and every contract with its current price list and key dates."""
    sites = D["sites"][D["sites"].customer_id == cid]
    con = roll_renewals(D["con"][D["con"].customer_id == cid])
    ver = D["ver"][D["ver"].contract_id.isin(con.contract_id)].sort_values("version_no").groupby("contract_id").tail(1).set_index("contract_id")
    nver = D["ver"][D["ver"].contract_id.isin(con.contract_id)].groupby("contract_id").size()
    opname = D["ops"].set_index("operator_id").operator_name
    site_name = sites.set_index("site_id").site_name
    devi = dev.set_index("device_id")
    contracts = []
    for c in con.sort_values(["site_id", "contract_id"]).itertuples():
        v = ver.loc[c.contract_id] if c.contract_id in ver.index else None
        terms = {}
        if v is not None:
            for col, key, unit in PRICE_TERMS:
                raw = v.get(col)
                if raw is None or (isinstance(raw, float) and math.isnan(raw)) or raw == 0:
                    continue
                val = raw if unit == "km" else (raw / 100 if unit == "%" else raw / 100)
                terms[key] = {"value": r(val, 2), "unit": unit}
        contracts.append({
            "contract_id": c.contract_id, "device_id": c.device_id, "site": site_name.get(c.site_id, c.site_id).split(" (")[0],
            "stream": c.material_stream, "operator": opname.get(c.operator_id, c.operator_id).replace(" (fictional)", ""),
            "operator_id": c.operator_id, "volume_m3": r(devi.loc[c.device_id, "container_volume_m3"]) if c.device_id in devi.index else None,
            "collection_policy": devi.loc[c.device_id, "collection_policy"] if c.device_id in devi.index else None,
            "pricing_model": (v.pricing_model if v is not None else c.pricing_model_initial),
            "start": str(c.contract_start)[:10], "notice_days": int(c.notice_period_days),
            "renewal": c.renewal.strftime("%Y-%m-%d"), "notice_by": c.notice_by.strftime("%Y-%m-%d"),
            "versions": int(nver.get(c.contract_id, 1)),
            "price_from": str(v.effective_from)[:10] if v is not None else None,
            "change_reason": (v.change_reason if v is not None else None), "terms": terms,
        })
    return {
        "sites": [{"site_id": s_.site_id, "name": s_.site_name.split(" (")[0], "municipality": s_.municipality, "region": s_.region_code,
                   "round_trip_km": int(s_.billable_round_trip_km), "opening_hours": s_.opening_hours_local,
                   "open_days": s_.opening_days_mon_sun, "devices": int((dev.site_id == s_.site_id).sum())} for s_ in sites.itertuples()],
        "contracts": contracts,
        "contracts_trace": traced(len(contracts), "contracts",
                                  "one contract per machine; current price list = latest contract version; renewal = start + k × 3 years; notice by = renewal − notice period",
                                  "yksi sopimus per laite; voimassa oleva hinnasto = viimeisin sopimusversio; uusiutuminen = alku + k × 3 v; irtisanottava viimeistään = uusiutuminen − irtisanomisaika",
                                  ["contracts", "versions", "sites"], None, "Contracts are assumed to renew every 3 years (Europress briefing)", 0),
    }


# ----------------------------------------------------------------------------- weight model (versioned, retrainable)
SEASON_OF = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
             6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}
TRAIN_UNTIL = "2026-04-01"   # train on Oct–Mar, test on Apr–Sep (time split, no leakage)


def _wm_frame(D):
    """Clean weighbridge-verified lifts: single load, weighed tare, no side waste → ground-truth kg."""
    tk = D["tk"][(D["tk"].allocation_method == "SINGLE_LOAD") & (D["tk"].tare_method == "WEIGHED")]
    ev = D["ev"][D["ev"].status.isin(["COMPLETED", "PARTIAL"]) & ~D["ev"].driver_reported_side_waste.fillna(False)]
    f = ev.merge(tk[["collection_id", "allocated_net_kg"]], on="collection_id", how="inner")
    f = f[(f.device_fill_pct_before > 10) & (f.allocated_net_kg > 0)].copy()
    f["season"] = f.actual_at.dt.month.map(SEASON_OF)
    f["vol_full"] = f.device_fill_pct_before / 100 * f.container_volume_m3
    f["train"] = f.actual_at < pd.Timestamp(TRAIN_UNTIL, tz="UTC")
    return f


def _metrics(pred, true):
    err = (pred - true) / true
    return {"median_abs_err_pct": r(err.abs().median() * 100, 1), "within_10_pct": r((err.abs() <= 0.10).mean() * 100, 0),
            "bias_pct": r(err.median() * 100, 1), "n": int(len(true))}


def train_weight_models(D):
    """v1 density table · v2 + learned density per stream × season · v3 + per-machine calibration gain."""
    f = _wm_frame(D)
    dens0 = pd.read_parquet(PACK / "reference/material_classes.parquet").set_index("material_stream").typical_compacted_density_kg_m3
    tr, te = f[f.train], f[~f.train]
    v1 = lambda x: x.vol_full * x.material_stream.map(dens0)
    dens_ss = (tr.allocated_net_kg / tr.vol_full).groupby([tr.material_stream, tr.season]).median()
    dens_s = (tr.allocated_net_kg / tr.vol_full).groupby(tr.material_stream).median()
    def season_density(stream, season):
        return dens_ss.get((stream, season), dens_s.get(stream, dens0.get(stream, 300)))
    v2 = lambda x: x.vol_full * [season_density(a, b) for a, b in zip(x.material_stream, x.season)]
    tr_ratio = (tr.allocated_net_kg / v2(tr)).groupby(tr.device_id)
    gain = tr_ratio.median()[tr_ratio.size() >= 3].clip(0.6, 1.6)
    v3 = lambda x: v2(x) * x.device_id.map(gain).fillna(1.0).values
    preds = {"v1": v1, "v2": v2, "v3": v3}
    meta = [
        {"id": "v1", "features": ["fill", "volume", "density_table"], "trained": False},
        {"id": "v2", "features": ["fill", "volume", "stream", "season"], "trained": True},
        {"id": "v3", "features": ["fill", "volume", "stream", "season", "machine_calibration"], "trained": True},
        {"id": "v4", "features": ["fill", "volume", "stream", "season", "machine_calibration", "temperature", "humidity", "pressure_curve"],
         "trained": False, "planned": True},
    ]
    for m in meta:
        if m["id"] in preds:
            m["test"] = _metrics(preds[m["id"]](te), te.allocated_net_kg)
            m["train_n"] = int(len(tr))
    seas = {s: {se: r(v, 0) for se, v in grp.droplevel(0).items()} for s, grp in dens_ss.groupby(level=0)}
    model = {"versions": meta, "default": "v3", "train_period": f"Oct 2025 – Mar 2026", "test_period": "Apr – Sep 2026",
             "calibrated_machines": int(len(gain)), "density_by_stream_season": seas,
             "trace": traced(len(te), "lifts", "error = (predicted kg − weighbridge net kg) ÷ weighbridge net kg on clean tickets (single load, weighed tare, no side waste); trained Oct–Mar, tested Apr–Sep",
                             "virhe = (ennuste kg − vaa'an netto kg) ÷ vaa'an netto kg puhtailla punnituslipuilla (yksi kuorma, punnittu taara, ei sivujätettä); opetus loka–maalis, testi huhti–syys",
                             ["tickets", "events", "devices", "reference/material_classes.parquet"],
                             sample(te.assign(v1=v1(te).round(0), v2=pd.Series(v2(te), index=te.index).round(0), v3=pd.Series(v3(te), index=te.index).round(0)),
                                    ["collection_id", "device_id", "material_stream", "season", "device_fill_pct_before", "allocated_net_kg", "v1", "v2", "v3"]),
                             "Ground truth is the operator's weighbridge (itself ±40–80 kg); synthetic data", 0)}
    def predict_now(device_id, stream, vol, fill, month):
        x = pd.DataFrame({"device_id": [device_id], "material_stream": [stream], "vol_full": [fill / 100 * vol], "season": [SEASON_OF[month]]})
        out = {k: r(float(np.asarray(fn(x))[0]), 0) for k, fn in preds.items()}
        out["calibrated"] = bool(device_id in gain.index)   # v3 = v2 when the machine has < 3 clean tickets
        return out
    return model, predict_now


def _operator_stats(D):
    """Per operator: name, median order→truck hours and mean on-time rate (operator scorecard)."""
    sc = D["sc"].groupby("operator_id").agg(lead=("median_order_to_lift_hours", "median"), on_time=("on_time_rate", "mean"))
    names = D["ops"].set_index("operator_id").operator_name.str.replace(" (fictional)", "", regex=False)
    return {op: {"name": names.get(op, op), "lead_h": float(row.lead), "on_time": float(row.on_time)} for op, row in sc.iterrows()}


def _device_lead_hours(ev_dev, default_h):
    """Median hours from SMART's order to the truck for this machine's past on-call lifts."""
    on_call = ev_dev[(ev_dev.trigger_type == "ON_CALL_ORDER") & ev_dev.order_placed_at.notna() & ev_dev.actual_at.notna()]
    if len(on_call) < 3:
        return default_h
    return float(((on_call.actual_at - on_call.order_placed_at).dt.total_seconds() / 3600).median())


def _next_fixed_visit(d, last_sched, as_of):
    """Next date the operator's fixed timetable sends a truck (weekdays 0=Mon; per_week < 1 = every n weeks)."""
    days = [int(x) for x in str(d.fixed_schedule_weekdays).split(",") if x.strip().isdigit()]
    if not days:
        return None
    per_week = float(d.fixed_schedule_per_week or len(days))
    step = max(1, round(len(days) / per_week)) if per_week else 1
    for k in range(1, 7 * step * 2 + 1):
        dd = as_of + pd.Timedelta(days=k)
        if dd.weekday() in days and (last_sched is None or ((dd - last_sched).days // 7) % step == 0):
            return dd
    return None


def machines_now(D, dev):
    """Current state per machine + who sends the truck and when. Fill comes from telemetry; forecast,
    lead time and weight are DEMO estimates (production: Europress' SMART forecast + the weight team's model)."""
    tel = D["tel"][D["tel"].device_id.isin(dev.device_id)].copy()
    tel["date"] = pd.to_datetime(tel.date)
    as_of = tel.date.max()
    max_days = pd.read_parquet(PACK / "reference/material_classes.parquet").set_index("material_stream").max_days_between_collections
    ev = D["ev"][D["ev"].device_id.isin(dev.device_id)]
    done = ev[ev.status.isin(["COMPLETED", "PARTIAL"])]
    last_lift = done.groupby("device_id").actual_at.max().dt.tz_localize(None)
    last_sched = done[done.trigger_type == "SCHEDULED"].groupby("device_id").actual_at.max().dt.tz_localize(None).dt.normalize()
    ops = _operator_stats(D)
    site_name = D["sites"].set_index("site_id").site_name
    out = []
    for d in dev.itertuples():
        g = tel[tel.device_id == d.device_id].sort_values("date")
        if g.empty:
            continue
        rate = g[g.date > as_of - pd.Timedelta(days=14)].fill_pct_last.diff().clip(lower=0).median()
        rate = float(rate) if rate == rate and rate > 0 else None
        cur = float(g.fill_pct_last.iloc[-1])
        op = ops.get(d.operator_id, {"name": d.operator_id, "lead_h": 36.0, "on_time": None})
        lead_h = _device_lead_hours(ev[ev.device_id == d.device_id], op["lead_h"])
        on_call = d.collection_policy == "ON_CALL"
        thr = float(d.on_call_threshold_pct) if on_call and d.on_call_threshold_pct == d.on_call_threshold_pct else TARGET_FILL_PCT
        order_on = visit = None
        ordered = on_call and cur >= thr
        if on_call:
            days = 0.0 if ordered else ((thr - cur) / rate if rate else None)
            reason = "ordered" if ordered else "fill"
            if not ordered and d.device_id in last_lift.index and d.material_stream in max_days.index:
                left = max(0.0, float(max_days[d.material_stream]) - (as_of - last_lift[d.device_id]).days)
                if days is None or left < days:
                    days, reason = left, "interval"
            if days is not None and days < 120:
                order_on = as_of + pd.Timedelta(days=days)
                visit = order_on + pd.Timedelta(hours=lead_h)
        else:
            reason = "schedule"
            visit = _next_fixed_visit(d, last_sched.get(d.device_id), as_of)
        days_to = (visit - as_of).total_seconds() / 86400 if visit is not None else None
        visit_fill = min(100.0, cur + (rate or 0) * days_to) if days_to is not None else None
        out.append({
            "device_id": d.device_id, "stream": d.material_stream, "volume_m3": r(d.container_volume_m3),
            "equipment": d.equipment_class, "site": site_name.get(d.site_id, "").split(" (")[0],
            "fill_now": r(min(cur, TARGET_FILL_PCT), 0), "fill_sensor_pct": r(cur, 0), "fill_rate_per_day": r(rate, 1),
            "policy": d.collection_policy, "order_threshold_pct": r(thr, 0) if on_call else None,
            "schedule_weekdays": None if on_call else str(d.fixed_schedule_weekdays), "schedule_per_week": None if on_call else r(d.fixed_schedule_per_week, 2),
            "operator_id": d.operator_id, "operator": op["name"], "operator_on_time_pct": r(op["on_time"] * 100, 0) if op["on_time"] is not None else None,
            "lead_h": r(lead_h, 0), "pickup_ordered": bool(ordered), "pickup_reason": reason,
            "order_on": order_on.strftime("%Y-%m-%d") if order_on is not None else None,
            "truck_on": visit.strftime("%Y-%m-%d") if visit is not None else None,
            "visit_fill_pct": r(visit_fill, 0) if visit_fill is not None else None,
            "days_to_pickup": r(days_to, 1) if days_to is not None else None,
            "pickup_on": visit.strftime("%Y-%m-%d") if visit is not None else None,
            "days_to_full": r(days_to, 1) if days_to is not None else None,          # legacy alias
            "full_on": visit.strftime("%Y-%m-%d") if visit is not None else None,    # legacy alias
            "weight_kg_now": r(g.mass_kg_last_valid.iloc[-1], 0) if pd.notna(g.mass_kg_last_valid.iloc[-1]) else None,
            "weight_by_model": D["wm_predict"](d.device_id, d.material_stream, float(d.container_volume_m3), min(cur, TARGET_FILL_PCT), as_of.month),
            "as_of": as_of.strftime("%Y-%m-%d"),
        })
    return out


def period_compare(t_m, cost_m, lifts, lines, months):
    """Last 3 months vs the 3 before — what got worse, in numbers."""
    last, prev = months[-3:], months[-6:-3]
    def block(ms):
        tt = float(t_m.loc[ms].values.sum())
        rec = float(t_m.loc[ms, [c for c in t_m.columns if c in RECYCLED]].values.sum())
        mixed = float(t_m.loc[ms, "mixed_waste"].sum()) if "mixed_waste" in t_m else 0.0
        cost = float(cost_m.loc[ms].values.sum())
        lm = lifts[lifts.month.isin(ms)]
        fees = float(lines[lines.billing_period.isin(ms) & lines.line_type.isin(AVOIDABLE_FEE_LINES)].amount_eur.sum())
        return {"cost": r(cost, 0), "tonnes": r(tt, 1), "eur_per_t": r(cost / tt if tt else None, 0),
                "recycling_rate": r(rec / tt * 100 if tt else None, 1), "mixed_share": r(mixed / tt * 100 if tt else None, 1),
                "avg_fill": r(lm.device_fill_pct_before.mean(), 0), "fees": r(fees, 0), "lifts": int(len(lm))}
    return {"last": block(last), "prev": block(prev), "last_months": [last[0], last[-1]], "prev_months": [prev[0], prev[-1]]}


def problems(cmp_, levers, ops, grades):
    """Rule-based 'SMART sees your problems': each with the change in numbers and one recommended action."""
    L, P = cmp_["last"], cmp_["prev"]
    lev = {l["id"]: l["saving_eur"] or 0 for l in levers}
    out = []
    if L["recycling_rate"] is not None and P["recycling_rate"] is not None and L["recycling_rate"] < P["recycling_rate"] - 1:
        out.append({"id": "recycling_down", "from": P["recycling_rate"], "to": L["recycling_rate"], "unit": "%", "eur": lev.get("sorting"), "action": "sorting"})
    if L["eur_per_t"] and P["eur_per_t"] and L["eur_per_t"] > P["eur_per_t"] * 1.03:
        out.append({"id": "cost_per_t_up", "from": P["eur_per_t"], "to": L["eur_per_t"], "unit": "€/t", "eur": lev.get("tender"), "action": "tender"})
    if L["avg_fill"] is not None and L["avg_fill"] < 70:
        out.append({"id": "half_empty", "from": P["avg_fill"], "to": L["avg_fill"], "unit": "%", "eur": lev.get("early_lifts"), "action": "early_lifts"})
    if L["fees"] and L["fees"] > (P["fees"] or 0) * 1.1 and L["fees"] > 100:
        out.append({"id": "fees_up", "from": P["fees"], "to": L["fees"], "unit": "€", "eur": lev.get("avoidable_fees"), "action": "avoidable_fees"})
    n_qa = sum(grades.values())
    bad = grades.get("rejected", 0)
    if n_qa and bad / n_qa > REJECTED_ALERT_SHARE:
        out.append({"id": "quality", "from": None, "to": r(bad / n_qa * 100, 1), "unit": "%", "eur": None, "action": "sorting"})
    for o in ops:
        if pd.Timestamp(o["notice_by"]) < TODAY + pd.DateOffset(months=3):
            out.append({"id": "notice", "from": None, "to": o["notice_by"], "unit": "date", "eur": None, "action": "tender",
                        "operator": o["name"].replace(" (fictional)", "")})
    return out


LOCAL_TZ = "Europe/Helsinki"
WINTER, SUMMER = {"12", "01", "02"}, {"06", "07", "08"}


def hourly_activity() -> pd.DataFrame:
    """Hourly telemetry → per device × local weekday × hour × month: compaction cycles and fill gained.
    Compactions are started by people (safety rule), so they show when the customer works and throws waste."""
    files = sorted((PACK / "telemetry" / "telemetry_hourly").glob("**/*.parquet"))
    t = pd.concat([pd.read_parquet(f, columns=["device_id", "measured_at", "fill_pct", "compaction_cycles"]) for f in files], ignore_index=True)
    t = t.sort_values(["device_id", "measured_at"])
    t["gain"] = t.groupby("device_id").fill_pct.diff().clip(lower=0, upper=40).fillna(0)   # emptyings are negative → 0
    local = t.measured_at.dt.tz_convert(LOCAL_TZ)
    t["wd"], t["hr"], t["month"] = local.dt.day_of_week, local.dt.hour, local.dt.strftime("%Y-%m")
    return t.groupby(["device_id", "wd", "hr", "month"], as_index=False).agg(cycles=("compaction_cycles", "sum"), gain=("gain", "sum"))


def _pct_change(a, b):
    return (b - a) / a * 100 if a else None


def time_patterns(D, dev, lifts, months):
    """When does the work happen: fillings by weekday × hour, pickups by weekday/hour, seasons."""
    h = D["hourly"][D["hourly"].device_id.isin(dev.device_id)]
    heat = h.groupby(["wd", "hr"]).cycles.sum().unstack(fill_value=0).reindex(index=range(7), columns=range(24), fill_value=0)
    by_wd = h.groupby("wd").cycles.sum().reindex(range(7), fill_value=0)
    by_hr = h.groupby("hr").cycles.sum().reindex(range(24), fill_value=0)
    m = h.groupby("month").agg(cycles=("cycles", "sum"), gain=("gain", "sum")).reindex(months, fill_value=0)
    days_in = pd.Series({mo: pd.Period(mo).days_in_month for mo in months})
    per_day_gain = (m.gain / days_in / max(1, len(dev)))           # fill % gained per machine-day
    per_day_cycles = (m.cycles / days_in / max(1, len(dev)))
    pk = lifts.copy()
    loc = pk.actual_at.dt.tz_convert(LOCAL_TZ)
    pick_wd = loc.dt.day_of_week.value_counts().reindex(range(7), fill_value=0)
    pick_hr = loc.dt.hour.value_counts().reindex(range(24), fill_value=0)
    season = lambda ser, S: float(ser[[mo for mo in months if mo[-2:] in S]].mean()) if any(mo[-2:] in S for mo in months) else None
    w_gain, s_gain = season(per_day_gain, WINTER), season(per_day_gain, SUMMER)
    w_cyc, s_cyc = season(per_day_cycles, WINTER), season(per_day_cycles, SUMMER)
    roll = by_hr.rolling(3).sum().shift(-2)                         # busiest 3-hour window
    peak_start = int(roll.idxmax()) if roll.notna().any() else None
    return {
        "heat": heat.values.astype(int).tolist(),
        "fill_by_weekday": by_wd.astype(int).tolist(), "fill_by_hour": by_hr.astype(int).tolist(),
        "pickups_by_weekday": pick_wd.astype(int).tolist(), "pickups_by_hour": pick_hr.astype(int).tolist(),
        "gain_per_day_by_month": [r(v, 2) for v in per_day_gain], "cycles_per_day_by_month": [r(v, 1) for v in per_day_cycles],
        "season": {"winter_gain": r(w_gain, 2), "summer_gain": r(s_gain, 2), "gain_diff_pct": r(_pct_change(s_gain, w_gain), 0),
                   "winter_cycles": r(w_cyc, 1), "summer_cycles": r(s_cyc, 1), "cycles_diff_pct": r(_pct_change(s_cyc, w_cyc), 0)},
        "busiest_weekday": int(by_wd.idxmax()), "quietest_weekday": int(by_wd.idxmin()),
        "busiest_weekday_share": r(by_wd.max() / by_wd.sum() * 100 if by_wd.sum() else None, 0),
        "peak_hours": [peak_start, (peak_start + 3) % 24] if peak_start is not None else None,
        "peak_hours_share": r(roll.max() / by_hr.sum() * 100 if by_hr.sum() else None, 0),
        "pickup_weekday": int(pick_wd.idxmax()) if pick_wd.sum() else None,
        "trace": traced(int(h.cycles.sum()), "cycles", "Σ compaction cycles from hourly telemetry, grouped by local weekday × hour (Europe/Helsinki); fill gained = Σ positive hour-to-hour fill-level increases",
                        "Σ puristusjaksot tuntitelemetriasta paikallisen viikonpäivän × tunnin mukaan (Europe/Helsinki); täyttymä = Σ positiiviset tunnittaiset täyttöasteen nousut",
                        ["telemetry/telemetry_hourly/*", "events"], None, "A compaction is always started by a person (safety rule), so cycles show when waste is thrown in", 0),
    }


def monthly_ops(lifts, months):
    """Pickups and average fill at emptying per month."""
    g = lifts.groupby("month").agg(pickups=("collection_id", "count"), fill=("device_fill_pct_before", "mean")).reindex(months)
    return {"pickups": [int(v) if v == v else 0 for v in g.pickups], "fill_at_emptying": [r(v, 0) for v in g.fill]}


def trend_findings(p, months):
    """Plain-language trend findings: last 3 months vs first 3 months, plus the time patterns."""
    out = []
    mo = p["monthly"]
    tot = [sum(v[i] or 0 for v in mo["tonnes_by_stream"].values()) for i in range(len(months))]
    cost = [sum(v[i] or 0 for v in mo["cost_by_stream"].values()) for i in range(len(months))]
    first, last = slice(0, 3), slice(-3, None)
    avg = lambda a, s: (sum(x or 0 for x in a[s]) / len(a[s])) if a[s] else None
    if "mixed_waste" in mo["tonnes_by_stream"]:
        mx = mo["tonnes_by_stream"]["mixed_waste"]
        out.append({"id": "mixed", "a": r(avg(mx, first), 1), "b": r(avg(mx, last), 1), "share_a": r(avg(mo["mixed_share"], first), 0), "share_b": r(avg(mo["mixed_share"], last), 0)})
    eurt = [c / t if t else None for c, t in zip(cost, tot)]
    out.append({"id": "eurt", "a": r(avg(eurt, first), 0), "b": r(avg(eurt, last), 0)})
    out.append({"id": "fill", "a": r(avg(mo["fill_at_emptying"], first), 0), "b": r(avg(mo["fill_at_emptying"], last), 0)})
    out.append({"id": "pickups", "a": r(avg(mo["pickups"], first), 1), "b": r(avg(mo["pickups"], last), 1)})
    out.append({"id": "recycling", "a": r(avg(mo["recycling_rate"], first), 0), "b": r(avg(mo["recycling_rate"], last), 0)})
    tp = p["time_patterns"]
    out.append({"id": "weekday", "day": tp["busiest_weekday"], "share": tp["busiest_weekday_share"], "quiet": tp["quietest_weekday"]})
    if tp["peak_hours"]:
        out.append({"id": "hours", "from": tp["peak_hours"][0], "to": tp["peak_hours"][1], "share": tp["peak_hours_share"]})
    if tp["season"]["gain_diff_pct"] is not None:
        out.append({"id": "season", "diff": tp["season"]["gain_diff_pct"], "w": tp["season"]["winter_gain"], "s": tp["season"]["summer_gain"]})
    if tp["pickup_weekday"] is not None:
        out.append({"id": "pickupday", "day": tp["pickup_weekday"]})
    return out


def customer_answers(D, p, dev, lifts):
    """Ready-made answers to the customer's framework of questions (Europress, 2nd briefing):
    fleet use · mismatches · price · contract/SLA · recycling · CO₂ · safety. Each: status, numbers, steps, trace."""
    k, lev = p["kpis"], {l["id"]: l for l in p["levers"]}
    out = []
    fill = k["avg_fill"]["value"] or 0
    early = p.get("early_lifts_n", 0)
    out.append({"id": "fleet", "status": "good" if fill >= 75 else "warn" if fill >= 60 else "bad",
                "p": {"fill": r(fill, 0), "early": early, "lifts": k["lifts"]["value"], "eur": lev["early_lifts"]["saving_eur"]},
                "trace": "levers.early_lifts"})
    # No "mismatch" (invoice / weighbridge differences) or "price" (tender) answers: operators are Europress's partners.
    ops = p["operators"]
    worst = min(ops, key=lambda o: o["on_time_rate"] or 100) if ops else None
    soon = sorted(ops, key=lambda o: o["notice_by"])[0] if ops else None
    out.append({"id": "contract", "status": "good" if worst and (worst["on_time_rate"] or 0) >= 85 else "warn",
                "p": {"op": worst["name"].replace(" (fictional)", "") if worst else "–", "ontime": worst["on_time_rate"] if worst else None,
                      "wasted": sum(o["wasted_trips"] for o in ops), "missed": sum(o["missed"] for o in ops),
                      "notice": soon["notice_by"] if soon else None, "notice_op": soon["name"].replace(" (fictional)", "") if soon else None},
                "trace": "operators"})
    tf = {f["id"]: f for f in p.get("trend_findings", [])}
    mixed = tf.get("mixed", {})
    rej = p["load_acceptance"]
    rej_share = r(rej["rejected"] / max(1, rej["accepted"] + rej["rejected"]) * 100, 1) if (rej["accepted"] + rej["rejected"]) else None
    out.append({"id": "recycling", "status": "good" if (k["recycling_rate"]["value"] or 0) >= 60 else "warn",
                "p": {"rate": k["recycling_rate"]["value"], "mixed_a": mixed.get("share_a"), "mixed_b": mixed.get("share_b"),
                      "rejected": rej_share, "eur": lev.get("sorting", {}).get("saving_eur")}, "trace": "kpis.recycling_rate"})
    out.append({"id": "co2", "status": "info", "p": {"co2": k["co2"]["value"], "km": p.get("co2_avoidable_km")}, "trace": "kpis.co2"})
    tel = D["tel"][D["tel"].device_id.isin(dev.device_id)]
    full_h = int(tel.hours_full.sum()) if "hours_full" in tel else 0
    down_h = int(tel.hours_down.sum()) if "hours_down" in tel else 0
    out.append({"id": "safety", "status": "good" if full_h < 24 else "warn", "p": {"full_h": full_h, "down_h": down_h}, "trace": None})
    return out


def customer_payload(D, cid, bench, fleet_dd):
    cust = D["cust"].set_index("customer_id").loc[cid]
    dev = D["dev"][D["dev"].customer_id == cid]
    ev = D["ev"][D["ev"].customer_id == cid]
    lifts = ev[ev.status.isin(["COMPLETED", "PARTIAL"])]
    lines = D["lines"][D["lines"].customer_id == cid]
    months = sorted(D["inv"].billing_period.unique())
    streams = sorted(dev.material_stream.unique())

    cost_m = lines.groupby(["billing_period", "material_stream"]).amount_eur.sum().unstack(fill_value=0).reindex(months, fill_value=0)
    t_m = (lifts.groupby(["month", "material_stream"]).device_estimated_collected_kg.sum() / 1000).unstack(fill_value=0).reindex(months, fill_value=0)
    total_cost = lines.amount_eur.sum()
    total_t = float(t_m.values.sum())
    rec_t = t_m[[c for c in t_m.columns if c in RECYCLED]].sum(axis=1)
    month_t = t_m.sum(axis=1).replace(0, np.nan)
    rec_rate_m = rec_t / month_t * 100
    mixed_share_m = (t_m["mixed_waste"] if "mixed_waste" in t_m else pd.Series(0.0, index=months)) / month_t * 100
    h1, h2 = mixed_share_m.iloc[:6].mean(), mixed_share_m.iloc[6:].mean()
    credits = -lines[lines.line_type == "MATERIAL_CREDIT"].amount_eur.sum()

    early, avoid, early_by_device = lever_early_lifts(lifts, lines)
    # Operators are Europress's partners: no tender or invoice-dispute levers in the customer's savings.
    # The customer sees what SMART itself changes (pickup timing, fees, sorting) — never a verdict on the operator.
    levers = [early, lever_fees(lines), lever_sorting(cid, lines, t_m, fleet_dd, bench)]
    levers.sort(key=lambda x: -(x["saving_eur"] or 0))
    total_saving = sum(l["saving_eur"] or 0 for l in levers)

    ws = lifts.weight_reconciliation_status.value_counts()
    verified, differ, no_wb = (int(ws.get(k, 0)) for k in ("WITHIN_10PCT", "DIFFERENCE_GT_10PCT", "WEIGHBRIDGE_NOT_AVAILABLE"))
    weight = {"verified_within_10pct": verified, "difference_gt_10pct": differ, "device_only": no_wb,
              "other": int(len(lifts) - verified - differ - no_wb),
              # "checkable" = the pickup has a weight the customer can check that is not the operator's invoice
              "lifts": int(len(lifts)), "with_ticket": verified + differ,
              "with_ticket_or_device_weight": int((lifts.weight_reconciliation_status.isin(["WITHIN_10PCT", "DIFFERENCE_GT_10PCT"])
                                                  | lifts.device_estimated_collected_kg.notna()).sum()),
              "trace": traced(verified / max(1, len(lifts)) * 100, "%",
                              "lifts where device weight and weighbridge ticket agree within 10% ÷ all lifts",
                              "tyhjennykset, joissa laitteen paino ja vaa'an punnituslippu ovat 10 % sisällä ÷ kaikki tyhjennykset",
                              ["events", "tickets"], sample(lifts.sort_values("actual_at"), LIFT_COLS))}

    l2 = lifts[lifts.device_fill_pct_before > 20].copy()
    l2["density"] = l2.device_estimated_collected_kg / (l2.device_fill_pct_before / 100 * l2.container_volume_m3)
    dens = l2.groupby(["month", "material_stream"]).density.median().unstack().reindex(months)

    qa_c = D["qa"][D["qa"].customer_id == cid]
    grades = {"accepted": int((~qa_c.rejected).sum()), "rejected": int(qa_c.rejected.sum())}   # accepted vs rejected (burned)
    ops = customer_operators(D, cid, ev)

    km = D["sites"].set_index("site_id").billable_round_trip_km
    trips_km = float(ev[ev.status.isin(["COMPLETED", "PARTIAL", "WASTED_TRIP"])].site_id.map(km).sum())
    dev_site = dev.set_index("device_id").site_id
    avoid_km = float(sum(a * km.get(dev_site.get(d), 0) for d, a in avoid.items()))

    kpis = {
        "total_cost": traced(total_cost, "EUR", "Σ all invoice lines (VAT 0%), Oct 2025 – Sep 2026",
                             "Σ kaikki laskurivit (alv 0 %), loka 2025 – syys 2026", ["invoices", "lines"],
                             sample(lines.sort_values("amount_eur", ascending=False), LINE_COLS), n=0),
        "tonnes": traced(total_t, "t", "Σ device-estimated collected kg per lift ÷ 1000 (completed + partial lifts)",
                         "Σ laitteen arvioima tyhjennetty kg ÷ 1000 (valmiit + osittaiset tyhjennykset)", ["events", "telemetry"],
                         sample(lifts.sort_values("actual_at"), LIFT_COLS),
                         "Weight comes from the device weight model (simulated here); see 'Weight evidence'", 1),
        "eur_per_t": traced(total_cost / total_t if total_t else None, "EUR/t", "total cost ÷ total tonnes",
                            "kokonaiskustannus ÷ kokonaistonnit", ["lines", "events"], None, n=0),
        "recycling_rate": traced(rec_t.sum() / total_t * 100 if total_t else None, "%",
                                 "tonnes of cardboard, paper, plastics, bio ÷ all tonnes",
                                 "kartongin, paperin, muovien ja biojätteen tonnit ÷ kaikki tonnit", ["events", "devices"],
                                 sample(t_m.round(2).rename_axis("month").reset_index(), ["month"] + list(t_m.columns))),
        "avg_fill": traced(lifts.device_fill_pct_before.mean(), "%", "mean fill level just before emptying, all lifts",
                           "keskimääräinen täyttöaste juuri ennen tyhjennystä", ["events", "telemetry"],
                           sample(lifts.sort_values("device_fill_pct_before"), LIFT_COLS), n=0),
        "lifts": traced(len(lifts), "lifts", "count of completed + partial lifts", "valmiiden ja osittaisten tyhjennysten määrä",
                        ["events"], sample(ev.sort_values("actual_at"), ["collection_id", "device_id", "trigger_type", "status", "actual_at"]), n=0),
        "savings": traced(total_saving, "EUR/yr", "Σ of the three savings levers below (each one is clickable)",
                          "Σ alla olevista kolmesta säästövivusta (jokainen avattavissa)", ["lines", "events", "devices", "versions"], None,
                          "Estimates on synthetic data; prices are hypothetical", 0),
        "co2": traced(trips_km * CO2_KG_PER_KM / 1000, "t CO₂", f"Σ truck trips × billable round-trip km × {CO2_KG_PER_KM} kg CO₂/km",
                      f"Σ kuorma-autokäynnit × laskutettava edestakainen km × {CO2_KG_PER_KM} kg CO₂/km", ["events", "sites"], None,
                      f"{CO2_KG_PER_KM} kg CO₂/km for a diesel refuse truck", 1),
    }

    return {
        "customer": {"id": cid, "name": cust.customer_name.replace("Fictional ", ""), "sector": cust.primary_sector,
                     "n_sites": int(cust.n_sites), "n_devices": int(len(dev)), "streams": streams},
        "period": {"from": months[0], "to": months[-1], "months": months, **as_of_fields()},
        "kpis": kpis,
        "monthly": {
            "cost_by_stream": {s: [r(v, 0) for v in cost_m[s]] if s in cost_m else [0] * len(months) for s in streams},
            "tonnes_by_stream": {s: [r(v, 2) for v in t_m[s]] if s in t_m else [0] * len(months) for s in streams},
            "recycling_rate": [r(v) for v in rec_rate_m], "mixed_share": [r(v) for v in mixed_share_m],
            "density_by_stream": {s: [r(v, 0) for v in dens[s]] if s in dens else [None] * len(months) for s in streams},
            **monthly_ops(lifts, months),
        },
        "material_credits_eur": r(credits, 0),
        "levers": levers,
        "devices": device_rows(D, cid, bench),
        "operators": ops,
        "weight": weight,
        "load_acceptance": grades,
        "insights": build_insights(h1, h2, grades, ops, early["saving_eur"] or 0, avoid_km),
        "co2_avoidable_km": r(avoid_km, 0),
        "account": customer_account(D, cid, dev),
        "time_patterns": time_patterns(D, dev, lifts, months),
        "machines_now": machines_now(D, dev),
        "compare": (cmp_ := period_compare(t_m, cost_m, lifts, lines, months)),
        "problems": problems(cmp_, levers, ops, grades),
        "fill_hist": np.histogram(lifts.device_fill_pct_before.dropna(), bins=10, range=(0, 100))[0].tolist(),
        "early_lifts_n": int((lifts.device_fill_pct_before < LOW_FILL_PCT).sum()),
        "wasted_trips_n": int((ev.status == "WASTED_TRIP").sum()),
    }


# ----------------------------------------------------------------------------- operator view
def operator_forecast(D, dev):
    tel = D["tel"][D["tel"].device_id.isin(dev.device_id)].copy()
    tel["date"] = pd.to_datetime(tel.date)
    last = tel[tel.date > tel.date.max() - pd.Timedelta(days=14)]
    devi = dev.set_index("device_id")
    rows = []
    for dv, g in last.groupby("device_id"):
        g = g.sort_values("date")
        rate = g.fill_pct_last.diff().clip(lower=0).median()
        cur = g.fill_pct_last.iloc[-1]
        mass = g.mass_kg_last_valid.iloc[-1]
        days = (TARGET_FILL_PCT - cur) / rate if rate and rate > 0 else 99.0
        exp_kg = mass * TARGET_FILL_PCT / cur if cur and cur > 5 and mass == mass else None
        rows.append({"device_id": dv, "customer_id": devi.loc[dv, "customer_id"], "stream": devi.loc[dv, "material_stream"],
                     "fill_now": r(cur, 0), "fill_rate_per_day": r(rate, 1), "days_to_target": r(max(0.0, days), 1),
                     "expected_kg_at_pickup": r(exp_kg, 0)})
    return pd.DataFrame(rows)


def operator_payload(D, op):
    dev = D["dev"][D["dev"].operator_id == op]
    fc = operator_forecast(D, dev)
    nxt = fc[fc.days_to_target <= 14].copy()
    nxt["day"] = np.floor(nxt.days_to_target).astype(int)
    per_day = nxt.groupby("day").agg(pickups=("device_id", "count"), kg=("expected_kg_at_pickup", "sum")).reindex(range(15), fill_value=0)
    by_stream = nxt.groupby("stream").expected_kg_at_pickup.sum() / 1000
    ev = D["ev"][D["ev"].operator_id == op]
    con = roll_renewals(D["con"][D["con"].operator_id == op])
    ren = (con[con.renewal < TODAY + pd.DateOffset(months=12)]
           .groupby("customer_id").agg(contracts=("contract_id", "count"), renewal=("renewal", "min")).reset_index()
           .sort_values("renewal"))
    ren["renewal"] = ren.renewal.dt.strftime("%Y-%m-%d")
    lifts = ev[ev.status.isin(["COMPLETED", "PARTIAL"])]
    o = D["ops"].set_index("operator_id").loc[op]
    fc_cols = ["device_id", "customer_id", "stream", "fill_now", "fill_rate_per_day", "days_to_target", "expected_kg_at_pickup"]
    return {
        "operator": {"id": op, "name": o.operator_name, "regions": o.regions_served, "devices": int(len(dev)),
                     "customers": int(dev.customer_id.nunique()), "shares_weighbridge": bool(o.weighbridge_data_shared)},
        "forecast_per_day": {"pickups": per_day.pickups.astype(int).tolist(), "tonnes": [r(v / 1000, 1) for v in per_day.kg]},
        "forecast_by_stream_t": {k: r(v, 1) for k, v in by_stream.items()},
        "forecast_trace": traced(len(nxt), "pickups",
                                 f"devices whose fill reaches {TARGET_FILL_PCT:.0f}% within 14 days at their median daily fill growth (last 14 days of telemetry)",
                                 f"laitteet, joiden täyttö saavuttaa {TARGET_FILL_PCT:.0f} % 14 päivän sisällä viimeisen 14 päivän mediaanikasvulla",
                                 ["telemetry", "devices"], sample(nxt.sort_values("days_to_target"), fc_cols), n=0),
        "early_lift_share": traced((lifts.device_fill_pct_before < LOW_FILL_PCT).mean() * 100, "%",
                                   f"share of lifts emptied below {LOW_FILL_PCT:.0f}% fill",
                                   f"alle {LOW_FILL_PCT:.0f} % täyttöasteella tyhjennettyjen osuus", ["events"],
                                   sample(lifts.sort_values("device_fill_pct_before"),
                                          ["collection_id", "device_id", "device_fill_pct_before", "device_estimated_collected_kg"])),
        "wasted_trips": int((ev.status == "WASTED_TRIP").sum()),
        "renewals_12m": ren.to_dict("records"),
        "fill_at_pickup_hist": np.histogram(lifts.device_fill_pct_before.dropna(), bins=10, range=(0, 100))[0].tolist(),
        **operator_history(ev, lifts),
    }


HISTORY_WEEKS = 26


def operator_history(ev: pd.DataFrame, lifts: pd.DataFrame) -> dict:
    """What SMART collected for the operator (weekly, last HISTORY_WEEKS weeks) and what SMART is worth to them.

    smart_pickups = pickups needed had every machine been emptied at TARGET_FILL_PCT (Σ fill before ÷ target).
    """
    def week_of(ts):
        return pd.to_datetime(ts, utc=True).dt.tz_convert(None).dt.to_period("W-SUN").dt.start_time

    lf = lifts.assign(week=week_of(lifts.actual_at))
    weeks = pd.date_range(end=(TODAY - pd.Timedelta(days=1)).to_period("W-SUN").start_time, periods=HISTORY_WEEKS, freq="7D") - pd.Timedelta(days=7)  # last FULL week
    g = lf.groupby("week")
    hist = pd.DataFrame({
        "pickups": g.size(),
        "tonnes": g.device_estimated_collected_kg.sum() / 1000,
        "smart_pickups": g.device_fill_pct_before.sum() / TARGET_FILL_PCT,
    }).reindex(weeks, fill_value=0)
    wasted = ev[ev.status == "WASTED_TRIP"]
    hist["wasted"] = wasted.assign(week=week_of(wasted.actual_at)).groupby("week").size().reindex(weeks, fill_value=0)
    year_pickups = len(lifts)
    year_smart = float(np.ceil(lifts.device_fill_pct_before.fillna(TARGET_FILL_PCT).sum() / TARGET_FILL_PCT))
    # typical day: pickups per calendar day in the history window → per weekday (Mon=0) and on working days
    in_window = lf[(lf.week >= weeks[0]) & (lf.week <= weeks[-1])]
    days = pd.date_range(weeks[0], weeks[-1] + pd.Timedelta(days=6), freq="D")
    per_day = (pd.to_datetime(in_window.actual_at, utc=True).dt.tz_convert(None).dt.normalize()
               .value_counts().reindex(days, fill_value=0))
    weekday_avg = per_day.groupby(per_day.index.dayofweek).mean().reindex(range(7), fill_value=0)
    working = per_day[per_day.index.dayofweek < 5]
    return {
        "weekday_avg_pickups": [r(v, 1) for v in weekday_avg],
        "typical_day_pickups": r(working.median(), 1),
        "history_weekly": {"weeks": [w.strftime("%Y-%m-%d") for w in weeks], "pickups": hist.pickups.astype(int).tolist(),
                           "tonnes": [r(v, 1) for v in hist.tonnes], "smart_pickups": [r(v, 1) for v in hist.smart_pickups],
                           "wasted": hist.wasted.astype(int).tolist()},
        "value": {"pickups_year": int(year_pickups), "smart_pickups_year": int(year_smart),
                  "trips_saved_year": int(max(0, year_pickups - year_smart)),
                  "kg_per_pickup_now": r(lifts.device_estimated_collected_kg.mean(), 0),
                  "kg_per_pickup_smart": r(lifts.device_estimated_collected_kg.sum() / max(year_smart, 1), 0),
                  "wasted_trips_year": int((ev.status == "WASTED_TRIP").sum())},
    }


# ----------------------------------------------------------------------------- index
def segment_benchmark(payloads):
    rows = []
    for p in payloads:
        n = p["customer"]["n_devices"]
        rows.append({"seg": "small" if n <= 3 else ("medium" if n <= 10 else "large"),
                     "saving": p["kpis"]["savings"]["value"] or 0, "cost": p["kpis"]["total_cost"]["value"] or 0})
    g = pd.DataFrame(rows).groupby("seg").agg(customers=("saving", "size"), median_saving=("saving", "median"),
                                              median_cost=("cost", "median"))
    g["saving_pct"] = g.median_saving / g.median_cost * 100
    return {k: {kk: r(vv, 0) for kk, vv in v.items()} for k, v in g.to_dict("index").items()}


def pick_hero(payloads):
    def score(p):
        n = p["customer"]["n_devices"]
        rising = any(i["id"] == "mixed_rising" for i in p["insights"])
        return ((4 <= n <= 14) * 3 + ("mixed_waste" in p["customer"]["streams"]) * 2 + rising * 3
                + len(p["insights"]) + (p["kpis"]["savings"]["value"] or 0) / 5000)
    mid = [p for p in payloads if 4 <= p["customer"]["n_devices"] <= 14] or payloads
    return max(mid, key=score)["customer"]["id"]


def write_ai_workspace(D):
    shutil.rmtree(AI / "data", ignore_errors=True)
    shutil.copytree(OUT, AI / "data")
    lifts_dir = AI / "data" / "lifts"
    lifts_dir.mkdir(exist_ok=True)
    cols = ["collection_id", "device_id", "material_stream", "operator_id", "trigger_type", "status", "actual_at",
            "device_fill_pct_before", "device_estimated_collected_kg", "weight_reconciliation_status", "invoice_match_status"]
    for cid, g in D["ev"].groupby("customer_id"):
        g[cols].to_csv(lifts_dir / f"{cid}.csv", index=False)
    ml = (D["lines"].groupby(["customer_id", "billing_period", "device_id", "material_stream", "line_type"])
          .amount_eur.sum().round(2).reset_index())
    for cid, g in ml.groupby("customer_id"):
        g.to_csv(lifts_dir / f"{cid}_invoice_summary.csv", index=False)


def main():
    D = prepare()
    bench, fleet_dd = fleet_benchmarks(D)
    weight_model, D["wm_predict"] = train_weight_models(D)
    for sub in ("customers", "operators"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    payloads = []
    for cid in sorted(D["cust"].customer_id):
        p = customer_payload(D, cid, bench, fleet_dd)
        p["trend_findings"] = trend_findings(p, p["period"]["months"])
        _dev = D["dev"][D["dev"].customer_id == cid]
        _lifts = D["ev"][(D["ev"].customer_id == cid) & D["ev"].status.isin(["COMPLETED", "PARTIAL"])]
        p["answers"] = customer_answers(D, p, _dev, _lifts)
        payloads.append(p)
        (OUT / "customers" / f"{cid}.json").write_text(json.dumps(p, ensure_ascii=False))
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I (isolated) mode drops the script dir
    from fleet_data import fleet_payload, processor_extras  # lazy: fleet_data imports helpers from this module
    for op in sorted(D["ops"].operator_id):
        payload = {**operator_payload(D, op), "extras": processor_extras(D, op)}
        (OUT / "operators" / f"{op}.json").write_text(json.dumps(payload, ensure_ascii=False, default=str))
    (OUT / "fleet.json").write_text(json.dumps(fleet_payload(D, payloads), ensure_ascii=False, default=str))
    hero = pick_hero(payloads)
    index = {
        "hero": hero,
        "customers": [{"id": p["customer"]["id"], "name": p["customer"]["name"], "sector": p["customer"]["sector"],
                       "n_devices": p["customer"]["n_devices"]} for p in payloads],
        "operators": [{"id": o.operator_id, "name": o.operator_name} for o in D["ops"].itertuples()],
        "benchmarks": bench, "segments": segment_benchmark(payloads),
        "weight_model": weight_model,
        "assumptions": {"target_fill_pct": TARGET_FILL_PCT, "recyclable_share_in_mixed": RECYCLABLE_SHARE_IN_MIXED,
                        "co2_kg_per_km": CO2_KG_PER_KM, "contract_years": CONTRACT_YEARS, "today": str(TODAY.date())},
    }
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False))
    write_ai_workspace(D)
    print("hero:", hero, "| customers:", len(payloads), "| segments:", index["segments"])


if __name__ == "__main__":
    main()
