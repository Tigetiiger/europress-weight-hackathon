// "For waste operators" view: forecast workload, expected tonnes, fill-at-pickup, renewals.
import { t, fmt, date, sector, addStrings } from "./i18n.js";
import { opStreamChart, histChart, regionChart } from "./charts.js";
import { esc, openTrace } from "./drawer.js";
import { isFree } from "./plan.js";

const el = (id) => document.getElementById(id);

function kpi(icon, label, value, sub, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "kpi";
  b.innerHTML = `<div class="kpi-label"><span>${esc(label)}</span>${onClick ? '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4-4"/></svg>' : ""}</div>
    <div class="kpi-value">${value}</div><div class="kpi-sub">${esc(sub || "")}</div>`;
  if (onClick) b.addEventListener("click", onClick); else b.disabled = true;
  return b;
}

// ---------------------------------------------------------------- collected → predicted, and SMART's value
/* global Chart */
let historyChart = null;
const INK = "#000d61", SMART_LINE = "#1a7a47", FORECAST = "#9aa3c7";

/** Weekly pickups SMART collected (bars), what an 85 %-full rule needs (line), and the 14-day forecast (light bars). */
function renderHistory(o) {
  const h = o.history_weekly;
  const canvas = el("opHistoryChart");
  if (!h || !canvas) return;
  const fc = o.forecast_per_day.pickups;
  const fcWeeks = [fc.slice(0, 7), fc.slice(7, 14)].map((w) => w.reduce((a, b) => a + b, 0));
  const lastWeek = new Date(`${h.weeks[h.weeks.length - 1]}T12:00:00Z`);
  const nextLabels = [1, 2].map((k) => new Date(lastWeek.getTime() + k * 7 * 864e5).toISOString().slice(0, 10));
  const labels = [...h.weeks, ...nextLabels].map((d) => new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" }).format(new Date(`${d}T12:00:00Z`)));
  const pad = (arr, n) => [...arr, ...Array(n).fill(null)];
  historyChart?.destroy();
  historyChart = new Chart(canvas, {
    type: "bar",
    data: { labels, datasets: [
      { label: t("opx.collected"), data: pad(h.pickups, 2), backgroundColor: INK, borderRadius: 3, maxBarThickness: 18, order: 2 },
      { label: t("opx.forecast"), hidden: isFree(), data: [...Array(h.weeks.length).fill(null), ...fcWeeks], backgroundColor: FORECAST, borderRadius: 3, maxBarThickness: 18, order: 2 },
      { type: "line", label: t("opx.smart"), data: pad(h.smart_pickups, 2), borderColor: SMART_LINE, backgroundColor: SMART_LINE, borderDash: [5, 4], borderWidth: 2, pointRadius: 0, tension: .3, order: 1 },
    ] },
    options: {
      maintainAspectRatio: false, animation: false,
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw, 0)}` } } },
      scales: { x: { stacked: true, grid: { display: false }, ticks: { maxTicksLimit: 10 } }, y: { stacked: false, beginAtZero: true, grid: { color: "#e2e5ec" }, border: { display: false } } },
    },
  });
}

// ---------------------------------------------------------------- driver plan (next 14 days)
const PICKUPS_PER_DRIVER_DAY = 6;     // hook-lift container exchanges one driver does in a day [assumption]
const FIRST_DAY_DOW = 3;              // forecast day 0 = 1 Oct 2026, a Thursday (Mon = 0)
const FIRST_DAY = "2026-10-01";
let driverChart = null;

function driverPlan(o) {
  const avg = o.weekday_avg_pickups, typical = o.typical_day_pickups;
  if (!avg || typical == null) return null;
  const crew = Math.max(1, Math.ceil(typical / PICKUPS_PER_DRIVER_DAY));
  // machines that reach 85 % at the weekend are emptied on the next Monday (operators rarely drive at weekends)
  const smartByDay = o.forecast_per_day.pickups.slice(0, 14);
  const carried = smartByDay.map(() => 0);
  smartByDay.forEach((n, i) => {
    const dow = (FIRST_DAY_DOW + i) % 7;
    const to = dow >= 5 ? i + (7 - dow) : i;
    if (to < carried.length) carried[to] += n;
  });
  const days = carried.map((smart, i) => {
    const dow = (FIRST_DAY_DOW + i) % 7;
    const expected = Math.max(avg[dow], smart);
    const drivers = Math.ceil(expected / PICKUPS_PER_DRIVER_DAY);
    const d = new Date(`${FIRST_DAY}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + i);
    return { i, dow, date: d, expected, smart, drivers, extra: Math.max(0, drivers - crew) };
  });
  const peaks = days.filter((d) => d.extra > 0);
  const quiet = days.filter((d) => d.expected < typical * 0.5 && d.dow < 5);
  const mondayExcess = Math.round(Math.max(0, avg[0] - typical));
  return { crew, typical, days, peaks, quiet, mondayExcess };
}

function renderDriverPlan(o) {
  const box = el("opDrivers");
  const plan = driverPlan(o);
  if (!box || !plan) return;
  const dayFmt = (d, style = "short") => new Intl.DateTimeFormat(undefined, { weekday: style, day: "numeric", month: "short", timeZone: "UTC" }).format(d.date);
  box.innerHTML = `<header class="card-head"><div><h2>${esc(t("opd.title"))}</h2><p class="muted">${esc(t("opd.sub", { crew: plan.crew, per: PICKUPS_PER_DRIVER_DAY }))}</p></div></header>
    <div class="chart chart-sm"><canvas id="opDriverChart"></canvas></div>
    <div class="opd-tips" id="opDriverTips">
      ${plan.peaks.length ? `<p><span class="state" data-state="bad">${esc(t("opd.callIn"))}</span>${plan.peaks.map((d) => `<b>${esc(dayFmt(d))}</b> +${d.extra}`).join(" · ")}</p>` : `<p><span class="state" data-state="good">✓</span>${esc(t("opd.noPeaks"))}</p>`}
      ${plan.quiet.length ? `<p><span class="state" data-state="info">${esc(t("opd.room"))}</span>${plan.quiet.map((d) => esc(dayFmt(d))).join(" · ")}</p>` : ""}
      ${plan.mondayExcess > 0 ? `<p class="opd-best"><b>✦ ${esc(t("opd.bestTitle"))}</b> ${esc(t("opd.best", { n: plan.mondayExcess }))}</p>` : ""}
    </div>`;
  driverChart?.destroy();
  driverChart = new Chart(el("opDriverChart"), {
    type: "bar",
    data: { labels: plan.days.map((d) => dayFmt(d, "short")), datasets: [
      { label: t("opd.drivers"), data: plan.days.map((d) => d.drivers), backgroundColor: plan.days.map((d) => (d.extra > 0 ? "#b43c3c" : INK)), borderRadius: 3, maxBarThickness: 22, order: 2 },
      { type: "line", label: t("opd.crew"), data: plan.days.map(() => plan.crew), borderColor: SMART_LINE, borderDash: [5, 4], borderWidth: 2, pointRadius: 0, order: 1 },
    ] },
    options: {
      maintainAspectRatio: false, animation: false,
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { afterLabel: (c) => (c.datasetIndex === 0
        ? t("opd.tip", { p: fmt(plan.days[c.dataIndex].expected, 0), s: plan.days[c.dataIndex].smart }) : "") } } },
      scales: { x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true } }, y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "#e2e5ec" }, border: { display: false } } },
    },
  });
}

addStrings({
  en: {
    "opd.title": "Driver plan — next 14 days", "opd.sub": "Drivers needed per day vs your usual crew of {crew} (≈ {per} pickups per driver a day, assumption).",
    "opd.drivers": "Drivers needed", "opd.crew": "Usual crew", "opd.tip": "≈ {p} pickups expected · {s} predicted by SMART fill levels",
    "opd.callIn": "Call in more drivers", "opd.noPeaks": "Your usual crew covers the next 14 days.", "opd.room": "Room for more work",
    "opd.bestTitle": "Best way:", "opd.best": "Mondays carry the weekend fill-up — about {n} pickups more than a normal day. Let SMART empty machines that are already 75 %+ full on Friday and the Monday peak flattens.",
  },
  fi: {
    "opd.title": "Kuljettajasuunnitelma — seuraavat 14 päivää", "opd.sub": "Tarvittavat kuljettajat päivittäin vs. tavallinen {crew} kuljettajan miehitys (≈ {per} tyhjennystä per kuljettaja päivässä, oletus).",
    "opd.drivers": "Tarvittavat kuljettajat", "opd.crew": "Tavallinen miehitys", "opd.tip": "≈ {p} tyhjennystä odotettavissa · {s} SMARTin täyttöennusteesta",
    "opd.callIn": "Kutsu lisää kuljettajia", "opd.noPeaks": "Tavallinen miehitys riittää seuraavat 14 päivää.", "opd.room": "Tilaa lisätyölle",
    "opd.bestTitle": "Paras tapa:", "opd.best": "Maanantaisin tyhjennetään viikonlopun täyttö — noin {n} tyhjennystä enemmän kuin tavallisena päivänä. Anna SMARTin tyhjentää jo 75 %+ täydet laitteet perjantaina, niin maanantain huippu tasoittuu.",
  },
});

function renderValue(o) {
  const v = o.value;
  const box = el("opValue");
  if (!v || !box) return;
  const fcPickups = o.forecast_per_day.pickups.reduce((a, b) => a + b, 0);
  const fcT = o.forecast_per_day.tonnes.reduce((a, b) => a + b, 0);
  const tile = (big, unit, label, sub) => `<div class="opv-tile"><b>${esc(big)}<small>${esc(unit)}</small></b><span class="opv-label">${esc(label)}</span><span class="opv-sub">${esc(sub)}</span></div>`;
  box.innerHTML = `<header class="card-head"><h2>${esc(t("opx.value"))}</h2></header>
    <div class="opv-grid">
      ${tile(fmt(v.trips_saved_year), "", t("opx.v.trips"), t("opx.v.tripsSub", { now: fmt(v.pickups_year), smart: fmt(v.smart_pickups_year) }))}
      ${tile(fmt(v.kg_per_pickup_smart), "kg", t("opx.v.load"), t("opx.v.loadSub", { now: fmt(v.kg_per_pickup_now) }))}
      ${tile(fmt(fcPickups), "", t("opx.v.plan"), t("opx.v.planSub", { t: fmt(fcT, 0) }))}
      ${tile(fmt(v.wasted_trips_year), "", t("opx.v.wasted"), t("opx.v.wastedSub"))}
    </div>
    <p class="opv-weight"><span class="state" data-state="good">✓</span>${esc(t("opx.v.weight"))}</p>
    <div class="opv-deal"><h3 class="sub-h">${esc(t("opx.deal"))}</h3><div class="opv-deal-grid">
      <div><span class="opv-label">${esc(t("opx.deal.give"))}</span><p>${esc(t("opx.deal.giveP"))}</p></div>
      <div><span class="opv-label">${esc(t("opx.deal.get"))}</span><p>${esc(t("opx.deal.getP"))}</p></div>
    </div><p class="fine">${esc(t("opx.deal.trust"))}</p></div>`;
}

addStrings({
  en: {
    "opx.graph": "Pickups: collected → predicted", "opx.graphSub": "Last 26 weeks collected, what SMART's 85 %-full rule would need, and the next 2 weeks predicted.",
    "opx.by": "Predicted by Europress SMART AI", "opx.collected": "Collected pickups", "opx.forecast": "Predicted pickups", "opx.smart": "Needed if emptied at 85 % full",
    "opx.value": "How SMART creates value for you",
    "opx.v.trips": "Fewer trips a year", "opx.v.tripsSub": "{now} pickups today → {smart} if every machine is emptied at 85 % full",
    "opx.v.load": "Fuller truck per pickup", "opx.v.loadSub": "Today {now} kg per pickup",
    "opx.v.plan": "Pickups known 14 days ahead", "opx.v.planSub": "≈ {t} t to collect — plan routes and trucks early",
    "opx.v.wasted": "Wasted trips to avoid", "opx.v.wastedSub": "SMART only orders a truck when the machine is really full",
    "opx.v.weight": "Every pickup weighed by the SMART weight model — the load is known before the truck leaves (within ±5 % on 73 % of test pickups).",
    "opx.deal": "The partnership", "opx.deal.give": "You share through SMART",
    "opx.deal.giveP": "Pickups, weighbridge weights and invoice lines for your SMART customers.",
    "opx.deal.get": "You get", "opx.deal.getP": "The driver plan, pickups predicted 14 days ahead and the load of every pickup before the truck leaves.",
    "opx.deal.trust": "Data is only exchanged when both parties agree. Europress never sells it to third parties.",
  },
  fi: {
    "opx.graph": "Tyhjennykset: kerätty → ennustettu", "opx.graphSub": "Viimeiset 26 viikkoa kerättynä, SMARTin 85 %:n säännön tarve ja seuraavat 2 viikkoa ennustettuna.",
    "opx.by": "Ennustanut Europress SMART AI", "opx.collected": "Kerätyt tyhjennykset", "opx.forecast": "Ennustetut tyhjennykset", "opx.smart": "Tarve, jos tyhjennetään 85 %:ssa",
    "opx.value": "Näin SMART tuottaa sinulle arvoa",
    "opx.v.trips": "Vähemmän ajoja vuodessa", "opx.v.tripsSub": "{now} tyhjennystä nyt → {smart}, jos jokainen laite tyhjennetään 85 %:ssa",
    "opx.v.load": "Täydempi kuorma per tyhjennys", "opx.v.loadSub": "Nyt {now} kg per tyhjennys",
    "opx.v.plan": "Tyhjennykset tiedossa 14 pv etukäteen", "opx.v.planSub": "≈ {t} t kerättävää — suunnittele reitit ja autot ajoissa",
    "opx.v.wasted": "Vältettävät turhat ajot", "opx.v.wastedSub": "SMART tilaa auton vain, kun laite on oikeasti täynnä",
    "opx.v.weight": "Jokainen tyhjennys punnittu SMART-painomallilla — kuorma tiedetään ennen kuin auto lähtee (±5 %:n sisällä 73 %:ssa testityhjennyksistä).",
    "opx.deal": "Kumppanuus", "opx.deal.give": "Jaat SMARTin kautta",
    "opx.deal.giveP": "SMART-asiakkaidesi tyhjennykset, punnituspainot ja laskurivit.",
    "opx.deal.get": "Saat", "opx.deal.getP": "Kuljettajasuunnitelman, tyhjennykset 14 päivää etukäteen ja jokaisen tyhjennyksen kuorman ennen kuin auto lähtee.",
    "opx.deal.trust": "Tietoja vaihdetaan vain, kun molemmat osapuolet hyväksyvät sen. Europress ei koskaan myy niitä kolmansille osapuolille.",
  },
});

export function renderOperator(o, customers) {
  renderDriverPlan(o);
  renderHistory(o);
  renderValue(o);
  const names = Object.fromEntries(customers.map((c) => [c.id, c]));
  const total = o.forecast_per_day.pickups.reduce((a, b) => a + b, 0);
  const k = el("opKpis");
  k.replaceChildren(
    kpi("i-space", t("op.devices"), fmt(o.operator.devices), `${o.operator.regions}`),
    kpi("i-mint", t("op.forecast"), fmt(total), `${fmt(o.forecast_per_day.tonnes.reduce((a, b) => a + b, 0), 0)} t`,
        () => openTrace(o.forecast_trace, t("op.forecast"), "forecast")),
    kpi("i-amber", t("op.early"), `${fmt(o.early_lift_share.value, 1)}<small>%</small>`, t("top.period"),
        () => openTrace(o.early_lift_share, t("op.early"), "early_lift_share")),
    kpi("i-red", t("op.wasted"), fmt(o.wasted_trips), `${fmt(o.operator.customers)} ${t("op.customers").toLowerCase()}`),
  );
  opStreamChart(o);
  histChart("opHistChart", o.fill_at_pickup_hist);
  if (o.extras) {
    regionChart("opRegionChart", o.extras.monthly_inbound_by_region);
    el("opHotspots").innerHTML = `<thead><tr><th>${t("top.customer")}</th><th class="num">${t("col.loads")}</th><th class="num">${t("col.cr")}</th></tr></thead>
      <tbody>${o.extras.quality_hotspots.map((h) => {
        const c = names[h.customer_id];
        return `<tr><td class="strong">${esc(c ? c.name : h.customer_id)}<span class="desc">${esc(c ? sector(c.sector) : "")}</span></td>
          <td class="num">${h.loads}</td><td class="num"><span class="state" data-state="${h.share >= 8 ? "bad" : "warn"}">${fmt(h.share, 1)} %</span></td></tr>`;
      }).join("")}</tbody>`;
  }
  const rows = o.renewals_12m.map((r) => {
    const c = names[r.customer_id];
    return `<tr><td class="strong">${esc(c ? c.name : r.customer_id)}<span class="desc">${esc(c ? sector(c.sector) : "")}</span></td>
      <td class="num">${r.contracts}</td><td class="num">${esc(date(r.renewal))}</td></tr>`;
  }).join("");
  el("renewalsTable").innerHTML = `<thead><tr><th>${t("top.customer")}</th><th class="num">${t("col.contracts")}</th><th class="num">${t("op.renewal")}</th></tr></thead><tbody>${rows}</tbody>`;
}
