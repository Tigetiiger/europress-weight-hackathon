// Europress internal workspace: fleet overview, sales pipeline, fleet health, data exchange (by agreement only).
import { t, fmt, eur, date, stream, sector, addStrings } from "./i18n.js";
import { flowChart, barChart, gradeChart, priceChart } from "./charts.js";
import { esc, openTrace, openCustom, closePanels } from "./drawer.js";

const el = (id) => document.getElementById(id);
const SEARCH_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4-4"/></svg>';

function kpi({ label, value, unit, sub, trace, primary, icon }) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = `kpi${primary ? " primary" : ""}`;
  b.innerHTML = `${icon ? `<img class="kpi-icon42" src="/img/icons/${icon}.svg" width="42" height="42" alt="">` : ""}<div class="kpi-label"><span>${esc(label)}</span>${trace ? SEARCH_ICON : ""}</div>
    <div class="kpi-value">${value}${unit ? `<small>${unit}</small>` : ""}</div><div class="kpi-sub">${esc(sub || "")}</div>`;
  if (trace) b.addEventListener("click", () => openTrace(trace, label, "fleet"));
  else b.disabled = true;
  return b;
}

export function renderEpOverview(f) {
  const k = f.kpis;
  el("epKpis").replaceChildren(
    kpi({ icon: "target", label: t("ep.k.pool"), value: eur(k.savings_pool.value), sub: t("ep.k.poolSub", { pct: `${Math.round((k.savings_pool.value / k.spend.value) * 100)} %` }), trace: k.savings_pool, primary: true }),
    kpi({ icon: "pie-chart", label: t("ep.k.spend"), value: eur(k.spend.value), sub: t("ep.k.customersSub", { n: k.customers }), trace: k.spend }),
    kpi({ icon: "system", label: t("ep.k.tonnes"), value: fmt(k.tonnes.value), unit: "t", sub: `${fmt(k.devices.value)} ${t("ep.k.devices").toLowerCase()}`, trace: k.tonnes }),
    kpi({ icon: "up", label: t("ep.k.fee"), value: eur(k.fee_pool.value), sub: t("ep.k.earlySub", { pct: fmt(k.early_share, 1) }), trace: k.fee_pool }),
  );
  flowChart("epFlowChart", f.data_products.months, f.data_products.monthly_t_by_stream);
  el("epTop").innerHTML = f.pipeline.slice(0, 6).map((c) =>
    `<li><span>${esc(c.name)}<span class="desc">${esc(sector(c.sector))} · ${c.devices}</span></span><b>${eur(c.savings)}</b></li>`).join("");
}

export function renderEpPipeline(f, onOpenCustomer) {
  const soon = new Date(f.period.notice_soon_before); // pipeline-derived from the data's as-of date
  const tbl = el("epPipelineTable");
  tbl.innerHTML = `<thead><tr><th>${t("col.customer")}</th><th class="num">${t("account.machines")}</th>
    <th class="num">${t("col.savings")}</th><th class="num">${t("col.fee")}</th><th class="num">${t("col.multiple")}</th><th>${t("col.topLever")}</th><th>${t("col.notice")}</th></tr></thead>
    <tbody>${f.pipeline.map((c, i) => `<tr class="click" data-i="${i}" tabindex="0">
      <td class="strong">${esc(c.name)}<span class="desc">${esc(sector(c.sector))}</span></td>
      <td class="num">${c.devices}</td><td class="num strong">${eur(c.savings)}</td><td class="num">${eur(c.fee)}</td>
      <td class="num">${eur(c.keeps)}</td><td>${esc(t(`lever.${c.top_lever}`))}</td>
      <td>${c.next_notice ? `<span class="state" data-state="${new Date(c.next_notice) < soon ? "bad" : "mute"}">${esc(date(c.next_notice))}</span>` : "–"}</td></tr>`).join("")}</tbody>`;
  tbl.querySelectorAll("tr.click").forEach((tr) => {
    const go = () => onOpenCustomer(f.pipeline[Number(tr.dataset.i)].id);
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
  });
}

export function renderEpFleet(f) {
  const m = f.maintenance;
  el("epFleetKpis").replaceChildren(
    kpi({ label: t("ep.f.downtime"), value: fmt(m.trace.value), unit: "h", sub: `${fmt(m.by_type.reduce((a, b) => a + b.orders, 0))} ${t("col.orders").toLowerCase()}`, trace: m.trace }),
    kpi({ label: t("ep.f.fix"), value: fmt(m.median_hours_to_fix, 1), unit: "h" }),
    kpi({ label: t("ep.f.recal"), value: fmt(m.recalibrations) }),
    kpi({ label: t("ep.f.data"), value: fmt(m.data_completeness, 1), unit: "%", sub: `${m.low_data_devices} < 95 %` }),
  );
  barChart("epWoChart", m.by_type.map((x) => x.type.replaceAll("_", " ").toLowerCase()), m.by_type.map((x) => x.orders), t("col.orders").toLowerCase(), true);
  el("epWoWrap").onclick = () => openTrace(m.trace, t("ep.wo"), "maintenance");
  el("epOverflowTable").innerHTML = `<thead><tr><th>${t("col.machine")}</th><th>${t("col.stream")}</th><th class="num">${t("col.hoursFull")}</th></tr></thead>
    <tbody>${m.overflow.map((d) => `<tr><td class="strong">${esc(d.device_id)}<span class="desc">${esc(d.customer_id)}</span></td>
      <td>${esc(stream(d.stream))}</td><td class="num">${fmt(d.hours_full)} h</td></tr>`).join("")}</tbody>`;
  el("epDowntimeTable").innerHTML = `<thead><tr><th>${t("col.machine")}</th><th>${t("col.stream")}</th><th class="num">${t("col.orders")}</th><th class="num">${t("col.downtime")}</th></tr></thead>
    <tbody>${m.top_downtime.map((d) => `<tr><td class="strong">${esc(d.device_id)}<span class="desc">${esc(d.customer_id)}</span></td>
      <td>${esc(stream(d.stream))}</td><td class="num">${d.orders}</td><td class="num">${fmt(d.downtime_h)} h</td></tr>`).join("")}</tbody>`;
}

// Europress never sells data: each party shares through SMART only what it agrees to, and gets value back.
const EXCHANGE = ["customer", "operator", "plant"];

export function renderEpData(f) {
  const d = f.data_products;
  el("epBuyersTable").innerHTML = `<thead><tr><th>${t("col.buyer")}</th><th>${t("col.product")}</th><th>${t("col.need")}</th></tr></thead>
    <tbody>${EXCHANGE.map((b) => `<tr><td class="strong">${esc(t(`xc.${b}`))}</td><td>${esc(t(`xc.${b}.give`))}</td><td>${esc(t(`xc.${b}.get`))}</td></tr>`).join("")}</tbody>`;
  gradeChart("epQualityChart", d.quality_by_stream);
  el("epQualityWrap").onclick = () => openTrace(d.quality_trace, t("ep.quality"), "quality");
  const cells = [...d.flow_cells].sort((a, b) => a.region_code.localeCompare(b.region_code) || b.tonnes - a.tonnes);
  el("epFlowTable").innerHTML = `<thead><tr><th>${t("col.region")}</th><th>${t("col.stream")}</th><th class="num">${t("col.tonnes")}</th>
    <th class="num">${t("col.customers")}</th><th>${t("col.publish")}</th></tr></thead><tbody>${cells.map((c) => `<tr>
      <td>${esc(c.region_code)}</td><td>${esc(stream(c.material_stream))}</td>
      <td class="num">${c.publishable ? fmt(c.tonnes) : "—"}</td><td class="num">${c.publishable ? c.customers : "&lt; 5"}</td>
      <td><span class="state" data-state="${c.publishable ? "good" : "mute"}">${esc(t(c.publishable ? "ep.publish.yes" : "ep.publish.no"))}</span></td></tr>`).join("")}</tbody>`;
  el("epFlowTrace").onclick = () => openTrace(d.flow_trace, t("ep.index"), "material_flow_index");
  priceChart("epPriceChart", d.months, d.price_index);
}

// ---------------------------------------------------------------- sales alerts (customer not following SMART advice)
const ALERT_STATE = { threshold: "warn", oncall: "warn", recycling: "note", tender: "info", capacity: "bad" };

function alertParams(a) {
  return { n: a.n, thr: a.min_thr, fill: a.fill, a: fmt(a.a, 1), b: fmt(a.b, 1), date: a.date ? date(a.date) : "", op: a.op || "", hours: fmt(a.hours), eur: eur(a.value) };
}

export function renderSalesAlerts(f, onAsk, onOpenCustomer) {
  const by = new Map();
  for (const a of f.sales_alerts || []) {
    const g = by.get(a.customer) || { name: a.name, devices: a.devices, customer: a.customer, alerts: [], value: 0 };
    g.alerts.push(a); g.value = Math.max(g.value, a.value || 0); by.set(a.customer, g);
  }
  const groups = [...by.values()].sort((x, y) => y.value - x.value).slice(0, 12);
  // one short row per lead; everything else lives in a structured pop-up
  el("salesAlerts").innerHTML = groups.map((g, i) => `<li class="ans lead" data-i="${i}">
      <button type="button" class="ans-row sa-row" aria-haspopup="dialog">
        <span class="state" data-state="${ALERT_STATE[g.alerts[0].type]}">${esc(t(`sa.${g.alerts[0].type}.tag`))}</span>
        <span class="ans-q">${esc(g.name)}</span>
        <b class="ans-h">${esc(t(`sa.${g.alerts[0].type}.offer`))}${g.alerts.length > 1 ? ` <span class="fine">+${g.alerts.length - 1}</span>` : ""}</b>
        <span class="eur">${g.value ? eur(g.value) : ""}</span>
      </button></li>`).join("");
  el("salesAlerts").querySelectorAll(".lead").forEach((li) => {
    const g = groups[Number(li.dataset.i)];
    li.querySelector(".ans-row").addEventListener("click", () => openLead(g, onAsk, onOpenCustomer));
  });
}

/** Structured lead pop-up: who → one block per opportunity (problem · offer · what to say · worth) → actions. */
function openLead(g, onAsk, onOpenCustomer) {
  const block = (a) => `<section class="lead-op">
      <header><span class="state" data-state="${ALERT_STATE[a.type]}">${esc(t(`sa.${a.type}.tag`))}</span>${a.value ? `<b class="eur">${esc(eur(a.value))} <small>${esc(t("lead.perYear"))}</small></b>` : ""}</header>
      <dl class="lead-dl">
        <div><dt>${esc(t("lead.problem"))}</dt><dd>${esc(t(`sa.${a.type}.why`, alertParams(a)))}</dd></div>
        <div><dt>${esc(t("sa.offerLabel"))}</dt><dd class="strong">${esc(t(`sa.${a.type}.offer`))}</dd></div>
        <div><dt>${esc(t("sa.tell"))}</dt><dd class="lead-say">“${esc(t(`sa.${a.type}.say`, alertParams(a)))}”</dd></div>
        ${a.date ? `<div><dt>${esc(t("lead.deadline"))}</dt><dd>${esc(date(a.date))}</dd></div>` : ""}
      </dl></section>`;
  const body = openCustom({
    eyebrow: t("lead.eyebrow"), title: g.name,
    question: t("sa.briefQ", { name: g.name, id: g.customer }), focus: `sales_alerts.${g.customer}`,
    html: `<dl class="lead-facts"><div><dt>${esc(t("account.machines"))}</dt><dd>${fmt(g.devices)}</dd></div>
        <div><dt>${esc(t("lead.value"))}</dt><dd>${esc(eur(g.value))}</dd></div><div><dt>${esc(t("lead.count"))}</dt><dd>${fmt(g.alerts.length)}</dd></div></dl>
      ${g.alerts.map(block).join("")}
      <div class="lead-actions"><button type="button" class="btn" data-lead="brief">${esc(t("sa.brief"))}</button>
        <button type="button" class="btn ghost" data-lead="open">${esc(t("lead.open"))}</button></div>`,
  });
  body.querySelector('[data-lead="brief"]').addEventListener("click", () => { closePanels(); onAsk?.(t("sa.briefQ", { name: g.name, id: g.customer })); });
  body.querySelector('[data-lead="open"]').addEventListener("click", () => { closePanels(); onOpenCustomer?.(g.customer); });
}

addStrings({
  en: { "lead.eyebrow": "Sales lead", "lead.problem": "Problem", "lead.deadline": "Deadline", "lead.value": "Worth to the customer / yr",
        "lead.count": "Opportunities", "lead.open": "Open customer dashboard", "lead.perYear": "/ yr" },
  fi: { "lead.eyebrow": "Myyntiliidi", "lead.problem": "Ongelma", "lead.deadline": "Määräaika", "lead.value": "Arvo asiakkaalle / v",
        "lead.count": "Mahdollisuudet", "lead.open": "Avaa asiakkaan näkymä", "lead.perYear": "/ v" },
});
