// Customer-facing weight: the hydraulic soft sensor for the customer's own machines, in plain words.
// Demo pairing: each customer machine (CMP-…) is shown with one simulated machine (BIN-…) of the same
// waste type and the nearest volume — the soft sensor runs on simulator telemetry (synthetic data).
import { t, fmt, eur, stream, addStrings } from "../i18n.js";
import { esc } from "../drawer.js";
import { sensorGet, fmtTime, kg } from "./api.js";

/** The one weight source: which trained soft-sensor model weighs each waste type (null = not trained yet). */
const MODEL_FOR = { cardboard: "cardboard", paper: "paper", plastic_film: "plastic_film", mixed_waste: "mixed_waste", mixed_plastic: "plastic_film" };
export const hasWeightModel = (streamKey) => Boolean(MODEL_FOR[streamKey]);
import { massChart } from "./charts.js";
import "./strings.js";

const CHART_HOURS = 720;
const TWIN_POOL = 12;           // closest volumes considered, then the nearest current fill wins
const MIN_TWIN_FILL = 15;       // a just-emptied twin says little about density
let metaPromise = null;
let fleetPromise = null;

addStrings({
  en: {
    "cw.back": "← Weight of all machines", "cw.notice": "Weight from the SMART weight model. Demo: computed on a simulated machine of the same type and size, scaled to your fill level. The chart and past pickups show that simulated machine.",
    "cw.now": "In the container now", "cw.nowSub": "Most likely {lo}–{hi} kg",
    "cw.next": "At the next pickup", "cw.nextSub": "{date} · when about {fill} % full",
    "cw.cost": "Expected charge", "cw.costSub": "Next pickup at your price {eurt} €/t",
    "cw.chart": "Weight in the container, last 30 days", "cw.history": "Last pickups: estimated weight",
    "cw.trust": "Approximate weight from the SMART weight model. In testing on machines it had never seen, it was about {pct} % off on average.", "cw.noLifts": "No pickups in the last 30 days.",
    "cw.tech": "How the model knows (technical)", "cw.noModel": "The weight model is not trained for {stream} yet (trained: cardboard, paper, plastic film, mixed waste; mixed plastic uses the plastic film model).",
    "cw.offline": "The weight model is not reachable right now. Fill levels and pickups still work.",
    "cw.card": "See weight & next bill →", "cw.title": "{name} · weight", "cw.when": "When", "cw.ours": "Our estimate", "cw.charge": "Expected charge",
  },
  fi: {
    "cw.back": "← Kaikkien laitteiden paino", "cw.notice": "Paino SMART-painomallista. Demo: laskettu samantyyppisellä ja -kokoisella simuloidulla laitteella ja skaalattu täyttöasteeseesi. Kaavio ja aiemmat tyhjennykset näyttävät simuloidun laitteen.",
    "cw.now": "Säiliössä nyt", "cw.nowSub": "Todennäköisesti {lo}–{hi} kg",
    "cw.next": "Seuraavassa tyhjennyksessä", "cw.nextSub": "{date} · kun noin {fill} % täynnä",
    "cw.cost": "Odotettu veloitus", "cw.costSub": "Seuraava tyhjennys hinnallasi {eurt} €/t",
    "cw.chart": "Paino säiliössä, viimeiset 30 päivää", "cw.history": "Viimeiset tyhjennykset: arvioitu paino",
    "cw.trust": "Likimääräinen paino SMART-painomallista. Testissä laitteilla, joita malli ei ollut nähnyt, se oli keskimäärin noin {pct} % pielessä.", "cw.noLifts": "Ei tyhjennyksiä viimeisen 30 päivän aikana.",
    "cw.tech": "Miten malli sen tietää (tekninen)", "cw.noModel": "Painomallia ei ole vielä opetettu jätelajille {stream} (opetettu: pahvi, paperi, muovikalvo, sekajäte; sekamuovi käyttää muovikalvomallia).",
    "cw.offline": "Painomalliin ei saada nyt yhteyttä. Täyttöasteet ja tyhjennykset toimivat.",
    "cw.card": "Katso paino ja seuraava lasku →", "cw.title": "{name} · paino", "cw.when": "Milloin", "cw.ours": "Arviomme", "cw.charge": "Odotettu veloitus",
  },
});

const getMeta = () => (metaPromise ??= sensorGet("meta").catch((e) => { metaPromise = null; throw e; }));
const getFleetNow = async () => {
  const meta = await getMeta();
  return (fleetPromise ??= sensorGet("fleet", { at: meta.replay.end }).catch((e) => { fleetPromise = null; throw e; }));
};

function hash(s) {
  let h = 2166136261;
  for (const ch of s) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return h >>> 0;
}

/**
 * Stable twin for a customer machine: same material, among the closest volumes the one whose fill right now
 * is nearest the customer's fill (ties broken by a hash of the id), so the model sees a similar state.
 */
export function pairBin(device, machines, fleetByBin) {
  const model = MODEL_FOR[device.stream];
  if (!model) return null;
  const fill = device.fill_now ?? 0;
  const pool = machines.filter((b) => b.material === model && (fleetByBin.get(b.bin_id)?.fill_pct ?? 0) >= MIN_TWIN_FILL)
    .sort((a, b) => Math.abs(a.container_volume_m3 - device.volume_m3) - Math.abs(b.container_volume_m3 - device.volume_m3) || a.bin_id.localeCompare(b.bin_id))
    .slice(0, TWIN_POOL);
  if (!pool.length) return null;
  const h = hash(String(device.device_id));
  return pool.reduce((best, b) => {
    const d = Math.abs((fleetByBin.get(b.bin_id).fill_pct) - fill) + ((hash(b.bin_id) ^ h) % 7) / 10;
    return d < best.d ? { d, id: b.bin_id } : best;
  }, { d: Infinity, id: null }).id;
}

/** Scale a twin's estimate to the customer's machine: same density, the customer's fill and volume. */
export function scaleFactor(device, twinRow, twinVolume) {
  const twinFill = Math.max(twinRow.fill_pct ?? 0, MIN_TWIN_FILL);
  return (Math.max(device.fill_now ?? 0, 0) / twinFill) * (device.volume_m3 / twinVolume);
}

/** Soft-sensor weight now per customer machine: Map device_id → {mass_kg, p10_kg, p90_kg, fill_pct, bin_id, factor}. */
export async function weightsNow(devices) {
  const [meta, fleet] = await Promise.all([getMeta(), getFleetNow()]);
  const byBin = new Map(fleet.machines.map((r) => [r.bin_id, r]));
  const vol = new Map(meta.machines.map((b) => [b.bin_id, b.container_volume_m3]));
  const out = new Map();
  for (const d of devices) {
    const bin = pairBin(d, meta.machines, byBin);
    const row = bin && byBin.get(bin);
    if (!row?.mass_kg) continue;
    const f = scaleFactor(d, row, vol.get(bin));
    out.set(d.device_id, { bin_id: bin, factor: f, fill_pct: d.fill_now, mass_kg: row.mass_kg * f, p10_kg: row.p10_kg * f, p90_kg: row.p90_kg * f });
  }
  return out;
}

/** Predicted mass when SMART empties the machine: today's density × the fill at pickup. */
function atPickup(row, m) {
  const fillNow = Math.max(row.fill_pct ?? m.fill_now ?? 0, 5);
  const fillAt = Math.max(fillNow, Math.min(100, m.visit_fill_pct ?? m.order_threshold_pct ?? 85));
  return { kg: (row.mass_kg / fillNow) * fillAt, fill: fillAt };
}

const answer = (label, value, sub, primary = false) => `<article class="kpi${primary ? " primary" : ""}">
  <div class="kpi-label">${esc(label)}</div><div class="kpi-value">${value}</div><div class="kpi-sub">${esc(sub)}</div></article>`;

/** Past pickups: only our approximate weight (and its charge) — never a comparison with the operator's ticket. */
function historyTable(lifts, eurPerT, meanPct) {
  if (!lifts.length) return `<p class="muted">${esc(t("cw.noLifts"))}</p>`;
  const rows = [...lifts].reverse().map((l) => `<tr><td>${esc(fmtTime(l.t))}</td><td class="num strong">${esc(kg(l.pred_kg))}</td>
      <td class="num">${eurPerT != null ? esc(eur((l.pred_kg / 1000) * eurPerT)) : "–"}</td></tr>`).join("");
  return `${meanPct != null ? `<p class="cw-trust">${esc(t("cw.trust", { pct: fmt(meanPct, 0) }))}</p>` : ""}
    <div class="table-wrap"><table class="table"><thead><tr><th>${esc(t("cw.when"))}</th><th class="num">${esc(t("cw.ours"))}</th>
    <th class="num">${esc(t("cw.charge"))}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

/** Mean % error of the model on machines it never saw (the honest figure for a customer's machine). */
const unseenMeanPct = (ev) => ev?.splits?.bins?.lift?.overall?.mean_pct ?? null;
const getEvaluation = () => fetch("/data/softsensor/evaluation.json").then((r) => (r.ok ? r.json() : null)).catch(() => null);

function techDetails(d) {
  const live = d.live;
  const inputs = Object.entries(live.inputs).map(([k, v]) => `<div><dt>${esc(t(`ss.in.${k}`))}</dt><dd>${esc(v == null ? "–" : fmt(v, Math.abs(v) < 10 ? 2 : 0))}</dd></div>`).join("");
  return `<details class="cw-tech"><summary>${esc(t("cw.tech"))}</summary>
    <ul class="ss-checks">
      <li><span class="state" data-state="info">${esc(fmt(live.ms))} ms</span>${esc(t("ss.m.ran", { rows: fmt(live.window.rows), strokes: fmt(live.window.strokes), ms: fmt(live.ms) }))}</li>
      <li><span class="state" data-state="good">${esc(t("ss.m.same"))}</span>${esc(t("ss.m.batch"))}: ${esc(kg(d.batch.mass_kg))}</li>
      <li><span class="state" data-state="note">sim</span>${esc(t("ss.m.truth"))}: ${esc(kg(d.truth_kg))} · ${esc(d.machine.bin_id)}</li>
    </ul>
    <h3 class="sub-h">${esc(t("ss.m.saw"))}</h3><dl class="ss-inputs">${inputs}</dl></details>`;
}

/**
 * Render the weight page for one customer machine.
 * @param {HTMLElement} root
 * @param {object} m machines_now row
 * @param {{name: string, eurPerT: number|null, dayLabel: (iso: string) => string}} opts
 */
export async function renderCustomerWeight(root, m, { name, eurPerT, dayLabel }) {
  root.innerHTML = `<a class="link" href="#weight">${esc(t("cw.back"))}</a>
    <p class="ss-notice" role="note"><span class="state" data-state="note">${esc(t("top.synthetic"))}</span>${esc(t("cw.notice"))}</p>
    <div id="cwBody"><p class="note">${esc(t("ss.loading"))}</p></div>`;
  const body = root.querySelector("#cwBody");
  if (!hasWeightModel(m.stream)) { body.innerHTML = `<p class="note">${esc(t("cw.noModel", { stream: stream(m.stream).toLowerCase() }))}</p>`; return; }
  let d, w, ev;
  try {
    const meta = await getMeta();
    ev = await getEvaluation();
    w = (await weightsNow([m])).get(m.device_id);
    if (!w) { body.innerHTML = `<p class="note">${esc(t("cw.offline"))}</p>`; return; }
    d = await sensorGet(`machines/${w.bin_id}`, { at: meta.replay.end, hours: CHART_HOURS });
  } catch {
    body.innerHTML = `<p class="note" data-state="bad">${esc(t("cw.offline"))}</p>`;
    return;
  }
  const next = atPickup(w, m);
  const cost = eurPerT != null ? (next.kg / 1000) * eurPerT : null;
  body.innerHTML = `
    <div class="kpis cw-answers">
      ${answer(t("cw.now"), `${esc(fmt(w.mass_kg))}<small>kg</small>`, t("cw.nowSub", { lo: fmt(w.p10_kg), hi: fmt(w.p90_kg) }), true)}
      ${answer(t("cw.next"), `${esc(fmt(next.kg, 0))}<small>kg</small>`, t("cw.nextSub", { date: m.truck_on ? dayLabel(m.truck_on) : "–", fill: fmt(next.fill) }))}
      ${answer(t("cw.cost"), cost != null ? esc(eur(cost)) : "–", t("cw.costSub", { eurt: fmt(eurPerT) }))}
    </div>
    <article class="card"><header class="card-head"><h2>${esc(t("cw.chart"))}</h2></header><div class="chart ss-chart-tall"><canvas id="cwChart"></canvas></div></article>
    <article class="card"><header class="card-head"><h2>${esc(t("cw.history"))}</h2></header>${historyTable(d.lifts, eurPerT, unseenMeanPct(ev))}${techDetails(d)}</article>`;
  massChart("cwChart", d.series, d.lifts, { customer: true });
}

// ---------------------------------------------------------------- Overview hero: weight first
const WEEK_DAYS = 7;
const addDays = (iso, n) => { const d = new Date(`${iso}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

/**
 * "Your waste right now": tonnes in the containers, next week's pickups and their expected charge,
 * and how accurate the model is on machines it never saw. Fills in asynchronously from the model service.
 */
export async function renderWeightHero(root, p, dayLabel) {
  root.innerHTML = `<header class="card-head"><div class="card-title"><h2>${esc(t("wh.title"))}</h2></div>
      <span class="ai-badge">${esc(t("wh.by"))}</span></header><div class="wh-body"><p class="muted">${esc(t("ss.loading"))}</p></div>`;
  const body = root.querySelector(".wh-body");
  let weights, evaluation;
  try {
    [weights, evaluation] = await Promise.all([weightsNow(p.machines_now), fetch("/data/softsensor/evaluation.json").then((r) => r.json())]);
  } catch {
    body.innerHTML = `<p class="note">${esc(t("cw.offline"))}</p>`;
    return;
  }
  const asOf = p.machines_now[0]?.as_of;
  const weekEnd = asOf ? addDays(asOf, WEEK_DAYS) : null;
  const price = new Map(p.devices.map((d) => [d.device_id, d.eur_per_t]));
  let nowKg = 0, nextKg = 0, nextEur = 0, pickups = 0, first = null;
  for (const m of p.machines_now) {
    const row = weights.get(m.device_id);
    if (!row?.mass_kg) continue;
    nowKg += row.mass_kg;
    if (m.truck_on && weekEnd && m.truck_on <= weekEnd) {
      const next = atPickup(row, m);
      nextKg += next.kg;
      nextEur += (next.kg / 1000) * (price.get(m.device_id) ?? 0);
      pickups += 1;
      if (!first || m.truck_on < first) first = m.truck_on;
    }
  }
  const meanPct = unseenMeanPct(evaluation);
  const big = (label, value, unit, sub, primary) => `<div class="wh-item${primary ? " primary" : ""}"><span class="wh-label">${esc(label)}</span>
    <b class="wh-value">${esc(value)}${unit ? `<small>${esc(unit)}</small>` : ""}</b><span class="wh-sub">${esc(sub)}</span></div>`;
  body.innerHTML = `<div class="wh-grid">
      ${big(t("wh.now"), fmt(nowKg / 1000, 1), "t", t("wh.nowSub", { n: weights.size, m: p.machines_now.length }), true)}
      ${big(t("wh.next"), fmt(nextKg / 1000, 1), "t", pickups ? t("wh.nextSub", { n: pickups, date: dayLabel(first) }) : t("wh.noPickups"))}
      ${big(t("wh.cost"), eur(nextEur), "", t("wh.costSub"))}
    </div>
    <p class="wh-trust">${meanPct != null ? esc(t("wh.trust", { pct: fmt(meanPct, 0) })) : ""}
      <a class="link" href="#weight">${esc(t("wh.see"))}</a></p>
    <p class="fine">${esc(t("cw.notice"))}</p>`;
}

addStrings({
  en: {
    "wh.title": "Live container weight", "wh.by": "Weighed by Europress SMART AI",
    "wh.now": "In your containers now", "wh.nowSub": "{n} of {m} machines weighed by the model",
    "wh.next": "Next 7 days of pickups", "wh.nextSub": "{n} pickups · first {date}", "wh.noPickups": "No pickups in the next 7 days",
    "wh.cost": "Expected charges, next 7 days", "wh.costSub": "Predicted weight × your price per tonne",
    "wh.trust": "Approximate weight. In testing on machines it had never seen, the model was about {pct} % off on average.", "wh.see": "See each machine →",
  },
  fi: {
    "wh.title": "Säiliöiden paino nyt", "wh.by": "Punninnut Europress SMART AI",
    "wh.now": "Säiliöissäsi nyt", "wh.nowSub": "Malli punnitsee {n}/{m} laitetta",
    "wh.next": "Tyhjennykset seuraavat 7 päivää", "wh.nextSub": "{n} tyhjennystä · ensimmäinen {date}", "wh.noPickups": "Ei tyhjennyksiä seuraavan 7 päivän aikana",
    "wh.cost": "Odotetut veloitukset, 7 päivää", "wh.costSub": "Ennustettu paino × tonnihintasi",
    "wh.trust": "Likimääräinen paino. Testissä laitteilla, joita malli ei ollut nähnyt, se oli keskimäärin noin {pct} % pielessä.", "wh.see": "Katso jokainen laite →",
  },
});

/** Weight page: one row per machine — weight now, next pickup, weight then, expected charge (→ machine page). */
export async function renderWeightTable(root, p, { names, dayLabel }) {
  root.innerHTML = `<header class="card-head"><div class="card-title"><h2>${esc(t("wt.title"))}</h2></div><span class="ai-badge">${esc(t("wh.by"))}</span></header>
    <div class="wt-body"><p class="muted">${esc(t("ss.loading"))}</p></div>`;
  const body = root.querySelector(".wt-body");
  let w;
  try { w = await weightsNow(p.machines_now); } catch { body.innerHTML = `<p class="note">${esc(t("cw.offline"))}</p>`; return; }
  const price = new Map(p.devices.map((d) => [d.device_id, d.eur_per_t]));
  const rows = [...p.machines_now].sort((a, b) => a.device_id.localeCompare(b.device_id)).map((m) => {
    const row = w.get(m.device_id);
    const next = row ? atPickup(row, m) : null;
    const cost = next && price.get(m.device_id) != null ? (next.kg / 1000) * price.get(m.device_id) : null;
    return { m, row, next, cost };
  });
  const tot = rows.reduce((a, r) => ({ now: a.now + (r.row?.mass_kg || 0), next: a.next + (r.next?.kg || 0), cost: a.cost + (r.cost || 0) }), { now: 0, next: 0, cost: 0 });
  body.innerHTML = `<div class="table-wrap"><table class="table"><thead><tr><th>${esc(t("ss.col.machine"))}</th><th class="num">${esc(t("mv.fillNow"))}</th>
    <th class="num">${esc(t("wt.now"))}</th><th>${esc(t("mv.nextTruck"))}</th><th class="num">${esc(t("wt.atPickup"))}</th><th class="num">${esc(t("cw.cost"))}</th></tr></thead><tbody>${
    rows.map(({ m, row, next, cost }) => `<tr${row ? ` class="click" tabindex="0" data-go="${esc(m.device_id)}"` : ""}>
      <td class="strong">${esc(names.get(m.device_id) || m.device_id)}<span class="desc">${esc(m.device_id)} · ${fmt(m.volume_m3)} m³</span></td>
      <td class="num">${fmt(m.fill_now)} %</td>
      <td class="num strong">${row ? esc(kg(row.mass_kg)) : `<span class="fine">${esc(t("wt.soon"))}</span>`}</td>
      <td>${m.truck_on ? esc(dayLabel(m.truck_on)) : "–"}</td>
      <td class="num">${next ? esc(kg(next.kg)) : "–"}</td>
      <td class="num strong">${cost != null ? esc(eur(cost)) : "–"}</td></tr>`).join("")}
    <tr class="wt-total"><td class="strong">${esc(t("total"))}</td><td></td><td class="num strong">${esc(kg(tot.now))}</td><td></td>
      <td class="num">${esc(kg(tot.next))}</td><td class="num strong">${esc(eur(tot.cost))}</td></tr></tbody></table></div>
    <p class="fine">${esc(t("wt.note"))}</p>`;
  body.querySelectorAll("tr[data-go]").forEach((tr) => {
    const go = () => { location.hash = `#machine/${encodeURIComponent(tr.dataset.go)}`; };
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
  });
}

addStrings({
  en: { "wt.title": "Weight of each machine, right now", "wt.now": "Weight now", "wt.atPickup": "Weight at pickup", "wt.soon": "model coming",
        "wt.note": "Click a machine for its weight history and expected charges. Biowaste: weight model not trained yet. Demo values on simulated machines." },
  fi: { "wt.title": "Jokaisen laitteen paino nyt", "wt.now": "Paino nyt", "wt.atPickup": "Paino tyhjennyksessä", "wt.soon": "malli tulossa",
        "wt.note": "Valitse laite nähdäksesi painohistorian ja odotetut veloitukset. Biojäte: painomallia ei ole vielä opetettu. Demoarvot simuloiduilla laitteilla." },
});

export const cardWeightLink = (deviceId) => `<a class="rt-weightlink" href="#machine/${encodeURIComponent(deviceId)}">${esc(t("cw.card"))}</a>`;
export const titleFor = (name) => t("cw.title", { name });
