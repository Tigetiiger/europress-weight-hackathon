"""Fleet-level (Europress internal) and processor-side data products.

Called from build_data.main(). Reuses the traced-value helpers so every number keeps its provenance.
"""
from __future__ import annotations

import pandas as pd

from build_data import LOW_FILL_PCT, SRC, TODAY, as_of_fields, load, r, sample, traced

SMART_SAVINGS_SHARE = 0.25  # SMART fee = a share of the savings SMART predicts for the customer; set per customer [demo assumption]
K_ANON_MIN_CUSTOMERS = 5               # publish a material-flow cell only with ≥5 customers
SRC.setdefault("maintenance", "operations/maintenance_work_orders.parquet")
SRC.setdefault("price_index", "reference/price_index_monthly.parquet")


def _lifts(D):
    return D["ev"][D["ev"].status.isin(["COMPLETED", "PARTIAL"])]


def fleet_kpis(D, payloads):
    lifts = _lifts(D)
    active = D["dev"][D["dev"].lifecycle_status == "ACTIVE"]
    spend = sum(p["kpis"]["total_cost"]["value"] or 0 for p in payloads)
    pool = sum(p["kpis"]["savings"]["value"] or 0 for p in payloads)
    fee_pool = pool * SMART_SAVINGS_SHARE
    cols = ["collection_id", "device_id", "material_stream", "actual_at", "device_fill_pct_before", "device_estimated_collected_kg"]
    return {
        "devices": traced(len(active), "machines", "active compactors and balers in the fleet", "aktiiviset puristimet ja paalaimet",
                          ["devices"], sample(active, ["device_id", "customer_id", "equipment_class", "material_stream", "collection_policy"]), n=0),
        "customers": int(D["dev"].customer_id.nunique()),
        "tonnes": traced(lifts.device_estimated_collected_kg.sum() / 1000, "t", "Σ device-estimated kg of all completed lifts ÷ 1000",
                         "Σ laitteiden arvioimat kg kaikista tyhjennyksistä ÷ 1000", ["events"], sample(lifts.sort_values("actual_at"), cols), n=0),
        "spend": traced(spend, "EUR", "Σ customers' invoiced waste-management cost, 12 months", "Σ asiakkaiden laskutetut jätehuoltokulut, 12 kk",
                        ["invoices", "lines"], None, n=0),
        "savings_pool": traced(pool, "EUR/yr", "Σ savings potential of all customers (three levers each)", "Σ kaikkien asiakkaiden säästöpotentiaali (kolme vipua)",
                               ["lines", "events", "devices"], None, "Synthetic data; prices hypothetical", 0),
        "fee_pool": traced(fee_pool, "EUR/yr", f"Σ customers' predicted savings × {SMART_SAVINGS_SHARE:.0%} (SMART fee = a share of what it saves)",
                           f"Σ asiakkaiden ennustetut säästöt × {SMART_SAVINGS_SHARE:.0%} (SMART-maksu = osuus säästöstä)", ["lines", "events", "devices"], None,
                           "The share is agreed per customer; 25 % is a demo assumption, not a Europress price", 0),
        "avg_fill": traced(lifts.device_fill_pct_before.mean(), "%", "mean fill at emptying across the fleet", "laitekannan keskimääräinen täyttöaste tyhjennettäessä",
                           ["events"], sample(lifts.sort_values("device_fill_pct_before"), cols), n=0),
        "early_share": r((lifts.device_fill_pct_before < LOW_FILL_PCT).mean() * 100),
        "wasted_trips": int((D["ev"].status == "WASTED_TRIP").sum()),
    }


def sales_pipeline(payloads):
    rows = []
    for p in payloads:
        n = p["customer"]["n_devices"]
        sav = p["kpis"]["savings"]["value"] or 0
        fee = sav * SMART_SAVINGS_SHARE
        notices = sorted(c["notice_by"] for c in p["account"]["contracts"])
        rows.append({"id": p["customer"]["id"], "name": p["customer"]["name"], "sector": p["customer"]["sector"], "devices": n,
                     "cost": p["kpis"]["total_cost"]["value"], "savings": sav, "fee": r(fee, 0), "keeps": r(sav - fee, 0),
                     "top_lever": p["levers"][0]["id"], "avg_fill": p["kpis"]["avg_fill"]["value"],
                     "next_notice": notices[0] if notices else None})
    rows.sort(key=lambda x: -(x["savings"] or 0))
    return rows


def maintenance(D):
    wo = load("maintenance")
    by_type = wo.groupby("work_order_type").agg(orders=("work_order_id", "count"), downtime_h=("downtime_hours", "sum")).reset_index()
    wo["hours_to_fix"] = (wo.completed_at - wo.reported_at).dt.total_seconds() / 3600
    devi = D["dev"].set_index("device_id")
    top = (wo.groupby("device_id").agg(orders=("work_order_id", "count"), downtime_h=("downtime_hours", "sum"))
           .sort_values("downtime_h", ascending=False).head(10).reset_index())
    top["customer_id"] = top.device_id.map(devi.customer_id)
    top["stream"] = top.device_id.map(devi.material_stream)
    full = D["tel"].groupby("device_id").agg(hours_full=("hours_full", "sum"), completeness=("data_completeness_pct", "mean")).reset_index()
    overflow = full.sort_values("hours_full", ascending=False).head(10).copy()
    overflow["customer_id"] = overflow.device_id.map(devi.customer_id)
    overflow["stream"] = overflow.device_id.map(devi.material_stream)
    return {
        "by_type": [{"type": x.work_order_type, "orders": int(x.orders), "downtime_h": r(x.downtime_h, 0)} for x in by_type.itertuples()],
        "median_hours_to_fix": r(wo.hours_to_fix.median(), 1),
        "recalibrations": int((wo.work_order_type == "SENSOR_RECALIBRATION").sum()),
        "top_downtime": top.round(1).to_dict("records"),
        "overflow": overflow.round(1).to_dict("records"),
        "low_data_devices": int((full.completeness < 95).sum()),
        "data_completeness": r(full.completeness.mean(), 1),
        "trace": traced(wo.downtime_hours.sum(), "h", "Σ downtime hours of all work orders (preventive, corrective, recalibration)",
                        "Σ työtilausten seisokkitunnit (ennakoiva, korjaava, kalibrointi)", ["maintenance"],
                        sample(wo.sort_values("downtime_hours", ascending=False),
                               ["work_order_id", "device_id", "work_order_type", "description_code", "downtime_hours", "reported_at", "completed_at"]), n=0),
    }


def data_products(D):
    """Greyparrot-style data products from source data: anonymised material-flow index + quality at the gate."""
    lifts = _lifts(D).merge(D["sites"][["site_id", "region_code"]], on="site_id", how="left")
    cell = (lifts.groupby(["region_code", "material_stream"])
            .agg(tonnes=("device_estimated_collected_kg", lambda s: s.sum() / 1000), customers=("customer_id", "nunique"),
                 lifts=("collection_id", "count"))
            .reset_index())
    cell["publishable"] = cell.customers >= K_ANON_MIN_CUSTOMERS
    cell["tonnes"] = cell.tonnes.round(1)
    months = sorted(D["inv"].billing_period.unique())
    mt = (lifts.groupby(["month", "material_stream"]).device_estimated_collected_kg.sum() / 1000).unstack(fill_value=0).reindex(months, fill_value=0)
    qa = D["qa"]
    # plants do not grade loads: each is accepted (recycled) or rejected (sent to incineration)
    grades = qa.assign(status=qa.rejected.map({True: "rejected", False: "accepted"})).groupby(["material_declared", "status"]).size().unstack(fill_value=0)
    pi = load("price_index")
    pidx = {m: [r(v, 1) for v in g.sort_values("month").set_index("month").price_eur_per_t.reindex(months)] for m, g in pi.groupby("material")}
    return {
        "flow_cells": [{k: (bool(v) if k == "publishable" else v) for k, v in row.items()} for row in cell.to_dict("records")],
        "flow_trace": traced(cell[cell.publishable].tonnes.sum(), "t",
                             f"Σ tonnes in region × stream cells with ≥{K_ANON_MIN_CUSTOMERS} distinct customers (k-anonymity); smaller cells withheld",
                             f"Σ tonnit alue × jae -soluissa, joissa ≥{K_ANON_MIN_CUSTOMERS} eri asiakasta (k-anonymiteetti); pienemmät solut piilotetaan",
                             ["events", "sites"], sample(cell.sort_values("tonnes", ascending=False), cell.columns.tolist(), 40),
                             "Volume only, no prices, no customer names — EU Data Act consent assumed in every contract", 0),
        "monthly_t_by_stream": {s: [r(v, 1) for v in mt[s]] for s in mt.columns},
        "quality_by_stream": {s: {g: int(n) for g, n in row.items()} for s, row in grades.iterrows()},
        "quality_trace": traced(len(qa), "assessments",
                                "loads accepted vs rejected at the plant (rejected = burned instead of recycled), linked to the source machine via ticket → job reference",
                                "laitoksella hyväksytyt vs hylätyt kuormat (hylätty = poltetaan kierrätyksen sijaan), yhdistetty lähdelaitteeseen punnituslipun ja työnumeron kautta",
                                ["quality", "tickets", "events"],
                                sample(qa, ["quality_assessment_id", "material_declared", "rejected", "remark_code"]), n=0),
        "price_index": pidx,
        "months": months,
    }


def processor_extras(D, op):
    """Extra blocks for the operator/processor view: monthly inbound tonnes by region × stream and quality at the gate."""
    lifts = _lifts(D)
    lifts = lifts[lifts.operator_id == op].merge(D["sites"][["site_id", "region_code"]], on="site_id", how="left")
    recent = lifts[lifts.actual_at >= lifts.actual_at.max() - pd.Timedelta(days=90)]
    region = (recent.groupby(["region_code", "material_stream"]).device_estimated_collected_kg.sum() / 1000 / 3).round(1)
    qa = D["qa"][D["qa"].operator_id == op]
    g = qa.assign(status=qa.rejected.map({True: "rejected", False: "accepted"})).groupby(["material_declared", "status"]).size().unstack(fill_value=0)
    qa = qa.assign(c_or_r=qa.rejected.astype(int))   # rejected = burned; no grades exist in practice
    cust = qa.groupby("customer_id").agg(loads=("c_or_r", "size"), c_or_r=("c_or_r", "sum"))
    cust["share"] = (cust.c_or_r / cust.loads * 100).round(1)
    hotspots = cust[cust.loads >= 10].sort_values("share", ascending=False).head(8).reset_index()
    return {
        "monthly_inbound_by_region": {reg: {s: float(v) for s, v in grp.droplevel(0).items()} for reg, grp in region.groupby(level=0)},
        "quality_by_stream": {s: {k: int(n) for k, n in row.items()} for s, row in g.iterrows()},
        "quality_hotspots": hotspots.to_dict("records"),
    }


def sales_alerts(payloads):
    """Flag customers who are not following SMART's recommendations → a concrete opportunity for sales,
    with the numbers and one sentence the salesperson can say to the customer."""
    out = []
    for p in payloads:
        c, lev = p["customer"], {l["id"]: l["saving_eur"] or 0 for l in p["levers"]}
        mn = p.get("machines_now", [])
        low = [m for m in mn if m.get("policy") == "ON_CALL" and m.get("stream") != "biowaste" and (m.get("order_threshold_pct") or 100) < 75]
        if low and lev.get("early_lifts", 0) > 2000:
            out.append({"type": "threshold", "value": lev["early_lifts"], "n": len(low), "min_thr": min(m["order_threshold_pct"] for m in low)})
            out[-1].update(customer=c["id"], name=c["name"], devices=c["n_devices"])
        half = [m for m in mn if m.get("policy") != "ON_CALL" and m.get("visit_fill_pct") is not None and m["visit_fill_pct"] < 60]
        if half:
            out.append({"type": "oncall", "value": lev.get("early_lifts", 0), "n": len(half), "fill": min(m["visit_fill_pct"] for m in half),
                        "customer": c["id"], "name": c["name"], "devices": c["n_devices"]})
        mixed = next((f for f in p.get("trend_findings", []) if f["id"] == "mixed"), None)
        if mixed and mixed.get("a") and mixed.get("b") and mixed["b"] > mixed["a"] * 1.1:
            out.append({"type": "recycling", "value": lev.get("sorting", 0), "a": mixed["a"], "b": mixed["b"],
                        "customer": c["id"], "name": c["name"], "devices": c["n_devices"]})
        # No "contract window / tender" alert: operators are Europress's partners.
        full_h = next((a["p"]["full_h"] for a in p.get("answers", []) if a["id"] == "safety"), 0)
        if full_h > 200:
            out.append({"type": "capacity", "value": 0, "hours": full_h, "customer": c["id"], "name": c["name"], "devices": c["n_devices"]})
    for a in out:
        a["value"] = r(a.get("value") or 0, 0)
    return sorted(out, key=lambda a: -(a["value"] or 0))


def fleet_payload(D, payloads):
    return {
        "kpis": fleet_kpis(D, payloads),
        "pipeline": sales_pipeline(payloads),
        "sales_alerts": sales_alerts(payloads),
        "maintenance": maintenance(D),
        "period": as_of_fields(),
        "data_products": data_products(D),
        "assumptions": {"fee_share_of_savings": SMART_SAVINGS_SHARE, "k_anon_min_customers": K_ANON_MIN_CUSTOMERS,
                        "today": str(TODAY.date())},
    }
