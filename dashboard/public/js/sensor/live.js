// Live soft-sensor view: replay clock over the held-out months, fleet table and one machine's live model run.
import { t, fmt } from "../i18n.js";
import { esc } from "../drawer.js";
import { sensorGet, fmtTime, kg, pct, since, material, MATERIALS, MAT_COLORS } from "./api.js";
import { massChart, destroyChart } from "./charts.js";

const HOUR_MS = 3_600_000;
const TICK_MS = 1000;
const START_OFFSET_H = 14 * 24;          // open two weeks into the window so charts have history
const SPEEDS = [1, 6, 24];               // replay hours per second
const WINDOWS = [168, 336, 720, 2160];   // chart window, hours
const PAGE = 25;
const SORTS = {
  mass: (a, b) => (b.mass_kg ?? -1) - (a.mass_kg ?? -1),
  fill: (a, b) => (b.fill_pct ?? -1) - (a.fill_pct ?? -1),
  density: (a, b) => (b.kg_m3 ?? -1) - (a.kg_m3 ?? -1),
  id: (a, b) => a.bin_id.localeCompare(b.bin_id),
};

const state = {
  meta: null, i: null, playing: false, speed: 1, timer: null, controller: null,
  bin: null, hours: 336, filter: "all", search: "", sort: "mass", limit: PAGE, fleet: null, root: null,
};

const nHours = () => state.meta.replay.hours;
const startMs = () => Date.parse(state.meta.replay.start);
const atMs = () => startMs() + state.i * HOUR_MS;
const atIso = () => new Date(atMs()).toISOString();
const q = (sel) => state.root?.querySelector(sel);

/** Jump the clock to the hour before `ms` (e.g. a lift) and open that machine. */
export function openMachineAt(bin, ms) {
  if (state.meta) state.i = Math.max(0, Math.min(nHours() - 1, Math.round((ms - startMs()) / HOUR_MS)));
  else state.pendingMs = ms;
  location.hash = `#ep-sensor/${bin}`;
}

export function stopLive() {
  pause();
  state.controller?.abort();
}

export async function renderLive(root, bin) {
  state.root = root;
  state.bin = bin || null;
  if (!state.meta) {
    root.innerHTML = `<p class="note">${esc(t("ss.loading"))}</p>`;
    try { state.meta = await sensorGet("meta"); } catch (e) { root.innerHTML = offline(e); return; }
    const pending = state.pendingMs ? Math.round((state.pendingMs - startMs()) / HOUR_MS) : START_OFFSET_H;
    state.i = Math.max(0, Math.min(nHours() - 1, pending));
  }
  root.innerHTML = clockHtml() + (state.bin ? machineSkeleton() : fleetSkeleton());
  bindClock();
  if (state.bin) bindMachine(); else bindFleet();
  await refresh();
}

const offline = (e) => `<p class="note" data-state="bad">${esc(t("ss.offline"))}<br><span class="fine">${esc(e.message)}</span></p>`;

// ---------------------------------------------------------------- replay clock
function clockHtml() {
  const speeds = SPEEDS.map((s) => `<button type="button" data-speed="${s}" aria-pressed="${s === state.speed}">${esc(s === 24 ? t("ss.speed.d") : t("ss.speed.h", { n: s }))}</button>`).join("");
  return `<article class="card ss-clock">
    <div class="ss-clock-time"><p class="eyebrow">${esc(t("ss.clock"))}</p><b id="ssNow">${esc(fmtTime(atMs()))}</b><p class="fine">${esc(t("ss.clock.sub"))}</p></div>
    <div class="ss-clock-ctl">
      <button type="button" class="icon-btn" id="ssBack" aria-label="${esc(t("ss.step.back"))}" title="${esc(t("ss.step.back"))}"><svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg></button>
      <button type="button" class="btn" id="ssPlay">${esc(state.playing ? t("ss.pause") : t("ss.play"))}</button>
      <button type="button" class="icon-btn" id="ssFwd" aria-label="${esc(t("ss.step.fwd"))}" title="${esc(t("ss.step.fwd"))}"><svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg></button>
      <div class="seg" role="group" aria-label="${esc(t("ss.speed"))}" id="ssSpeed">${speeds}</div>
    </div>
    <input type="range" class="ss-range" id="ssRange" min="0" max="${nHours() - 1}" value="${state.i}" aria-label="${esc(t("ss.clock"))}">
  </article>`;
}

function bindClock() {
  q("#ssPlay").addEventListener("click", () => (state.playing ? pause() : play()));
  q("#ssBack").addEventListener("click", () => step(-1));
  q("#ssFwd").addEventListener("click", () => step(1));
  q("#ssSpeed").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-speed]");
    if (!b) return;
    state.speed = Number(b.dataset.speed);
    q("#ssSpeed").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  });
  const range = q("#ssRange");
  range.addEventListener("input", () => { state.i = Number(range.value); syncClock(); });
  range.addEventListener("change", () => refresh());
}

function syncClock() {
  const now = q("#ssNow"), range = q("#ssRange"), playBtn = q("#ssPlay");
  if (now) now.textContent = fmtTime(atMs());
  if (range) range.value = String(state.i);
  if (playBtn) playBtn.textContent = state.playing ? t("ss.pause") : t("ss.play");
}

function step(delta) {
  pause();
  state.i = Math.max(0, Math.min(nHours() - 1, state.i + delta));
  syncClock();
  refresh();
}

function play() {
  if (state.i >= nHours() - 1) state.i = 0;
  state.playing = true;
  syncClock();
  loop();
}

function pause() {
  state.playing = false;
  clearTimeout(state.timer);
  syncClock();
}

async function loop() {
  if (!state.playing) return;
  const t0 = performance.now();
  state.i = Math.min(state.i + state.speed, nHours() - 1);
  syncClock();
  await refresh();
  if (state.i >= nHours() - 1) { pause(); return; }
  if (state.playing) state.timer = setTimeout(loop, Math.max(0, TICK_MS - (performance.now() - t0)));
}

async function refresh() {
  state.controller?.abort();
  const controller = new AbortController();
  state.controller = controller;
  try {
    if (state.bin) {
      const m = await sensorGet(`machines/${state.bin}`, { at: atIso(), hours: state.hours }, controller.signal);
      renderMachine(m);
    } else {
      state.fleet = await sensorGet("fleet", { at: atIso() }, controller.signal);
      renderFleet();
    }
  } catch (e) {
    if (e.name === "AbortError") return;
    pause();
    const target = q(state.bin ? "#ssMachine" : "#ssFleet");
    if (target) target.innerHTML = e.status === 404 ? `<p class="note">${esc(t("ss.m.notFound"))}</p>` : offline(e);
  }
}

// ---------------------------------------------------------------- fleet
function fleetSkeleton() {
  const mats = ["all", ...MATERIALS].map((m) => `<button type="button" data-mat="${m}" aria-pressed="${m === state.filter}">${esc(material(m))}</button>`).join("");
  const sorts = Object.keys(SORTS).map((s) => `<option value="${s}"${s === state.sort ? " selected" : ""}>${esc(t(`ss.sort.${s}`))}</option>`).join("");
  return `<div class="kpis" id="ssKpis"></div>
    <article class="card">
      <header class="card-head"><div><h2>${esc(t("ss.fleet"))}</h2><p class="muted" id="ssFleetSub"></p></div></header>
      <div class="ss-filters">
        <div class="seg ss-wrap" role="group" aria-label="${esc(t("ss.col.material"))}" id="ssMat">${mats}</div>
        <label class="ss-field"><span class="sr">${esc(t("ss.search"))}</span><input type="search" id="ssSearch" placeholder="${esc(t("ss.search"))}" value="${esc(state.search)}" maxlength="12"></label>
        <label class="ss-field"><span class="sr">${esc(t("ss.sort"))}</span><select id="ssSort">${sorts}</select></label>
      </div>
      <div id="ssFleet"></div>
    </article>`;
}

function bindFleet() {
  q("#ssMat").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-mat]");
    if (!b) return;
    state.filter = b.dataset.mat;
    state.limit = PAGE;
    q("#ssMat").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    renderFleet();
  });
  q("#ssSearch").addEventListener("input", (e) => { state.search = e.target.value.trim().toUpperCase(); state.limit = PAGE; renderFleet(); });
  q("#ssSort").addEventListener("change", (e) => { state.sort = e.target.value; renderFleet(); });
}

function kpiCard(label, value, unit, sub, primary = false) {
  return `<article class="kpi${primary ? " primary" : ""}"><div class="kpi-label">${esc(label)}</div>
    <div class="kpi-value">${esc(value)}${unit ? `<small>${esc(unit)}</small>` : ""}</div><div class="kpi-sub">${esc(sub)}</div></article>`;
}

function renderKpis(f) {
  const th = state.meta.thresholds;
  q("#ssKpis").innerHTML = [
    kpiCard(t("ss.k.mass"), fmt(f.totals.mass_t, 1), "t", t("ss.k.massSub", { t: fmt(f.evaluation.truth_t, 1) }), true),
    kpiCard(t("ss.k.ready"), fmt(f.totals.ready_to_empty), "", t("ss.k.readySub", { pct: th.order_fill_pct })),
    kpiCard(t("ss.k.heavy"), fmt(f.totals.heavy), "", t("ss.k.heavySub", { t: fmt(th.truck_payload_kg / 1000) })),
    kpiCard(t("ss.k.err"), fmt(f.evaluation.mae_kg), "kg", t("ss.k.errSub", { cov: fmt(f.evaluation.coverage * 100) })),
  ].join("");
}

function visibleRows(rows) {
  return rows
    .filter((r) => state.filter === "all" || r.material === state.filter)
    .filter((r) => !state.search || r.bin_id.includes(state.search))
    .sort(SORTS[state.sort]);
}

function fleetRow(r) {
  const ready = r.fill_pct != null && r.fill_pct >= state.meta.thresholds.order_fill_pct;
  return `<tr class="click" tabindex="0" data-bin="${esc(r.bin_id)}">
    <td class="strong">${esc(r.bin_id)}<span class="desc">${esc(r.equipment_class)} · ${fmt(r.volume_m3)} m³ · ${esc(r.region)}</span></td>
    <td><span class="dot" style="background:${MAT_COLORS[r.material]}"></span>${esc(material(r.material))}</td>
    <td><span class="fill"><span class="fill-bar" data-state="${ready ? "warn" : "good"}"><i style="width:${Math.min(100, r.fill_pct ?? 0)}%"></i></span>${esc(pct(r.fill_pct, 0))}</span></td>
    <td class="num strong">${esc(kg(r.mass_kg))}</td>
    <td class="num muted-cell">${esc(fmt(r.p10_kg))}–${esc(fmt(r.p90_kg))}</td>
    <td class="num">${esc(fmt(r.kg_m3))}</td>
    <td class="num">${esc(since(r.hours_since_lift))}</td>
    <td class="num muted-cell">${esc(kg(r.truth_kg))}</td></tr>`;
}

function renderFleet() {
  const f = state.fleet;
  if (!f || !q("#ssFleet")) return;
  renderKpis(f);
  q("#ssFleetSub").textContent = t("ss.fleet.sub", { n: fmt(f.machines.length) });
  const rows = visibleRows(f.machines);
  const shown = rows.slice(0, state.limit);
  const more = rows.length - shown.length;
  q("#ssFleet").innerHTML = `<div class="table-wrap"><table class="table">
    <thead><tr><th>${esc(t("ss.col.machine"))}</th><th>${esc(t("ss.col.material"))}</th><th>${esc(t("ss.col.fill"))}</th>
      <th class="num">${esc(t("ss.col.est"))}</th><th class="num">${esc(t("ss.col.band"))}</th><th class="num">${esc(t("ss.col.density"))}</th>
      <th class="num">${esc(t("ss.col.since"))}</th><th class="num">${esc(t("ss.col.truth"))}</th></tr></thead>
    <tbody>${shown.map(fleetRow).join("")}</tbody></table></div>
    ${more > 0 ? `<button type="button" class="btn ghost ss-more" id="ssMore">${esc(t("ss.more", { n: Math.min(PAGE, more) }))}</button>` : ""}`;
  q("#ssFleet").querySelectorAll("tr[data-bin]").forEach((tr) => {
    const go = () => { location.hash = `#ep-sensor/${tr.dataset.bin}`; };
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
  });
  q("#ssMore")?.addEventListener("click", () => { state.limit += PAGE; renderFleet(); });
}

// ---------------------------------------------------------------- machine
function machineSkeleton() {
  const wins = WINDOWS.map((h) => `<button type="button" data-hours="${h}" aria-pressed="${h === state.hours}">${esc(t("ss.days", { n: h / 24 }))}</button>`).join("");
  return `<div id="ssMachine">
    <article class="card ss-mhead" id="ssMHead"><a class="link" href="#ep-sensor">${esc(t("ss.back"))}</a></article>
    <div class="grid g-2-1">
      <article class="card">
        <header class="card-head"><h2>${esc(t("ss.m.title"))}</h2><div class="seg" role="group" aria-label="${esc(t("ss.m.window"))}" id="ssWin">${wins}</div></header>
        <div class="chart ss-chart-tall"><canvas id="ssMassChart"></canvas></div>
      </article>
      <article class="card" id="ssNowCard"></article>
    </div>
    <article class="card"><header class="card-head"><h2>${esc(t("ss.m.lifts"))}</h2></header><div id="ssLifts"></div></article>
  </div>`;
}

function bindMachine() {
  destroyChart("ssMassChart");
  q("#ssWin").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-hours]");
    if (!b) return;
    state.hours = Number(b.dataset.hours);
    q("#ssWin").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    refresh();
  });
}

function renderMachineHead(m) {
  const x = m.machine;
  const facts = [material(x.material), `${x.equipment_class} · ${fmt(x.container_volume_m3)} m³`, x.region, x.infeed?.replaceAll("_", " "),
    x.drainage ? "drainage" : null, x.site_id, x.press_model].filter(Boolean);
  q("#ssMHead").innerHTML = `<a class="link" href="#ep-sensor">${esc(t("ss.back"))}</a>
    <h2 class="ss-mtitle"><span class="dot" style="background:${MAT_COLORS[x.material]}"></span>${esc(x.bin_id)}</h2>
    <p class="muted">${facts.map(esc).join(" · ")}</p>`;
}

function renderNow(m) {
  const live = m.live, b = m.batch;
  const same = b.mass_kg != null && Math.abs(live.mass_kg - b.mass_kg) <= Math.max(0.5, b.mass_kg * 1e-4);
  const err = m.truth_kg ? ((live.mass_kg - m.truth_kg) / m.truth_kg) * 100 : null;
  const setup = t(`ss.m.setup.${live.model.setup}`, { group: material(live.model.group) });
  const inputs = Object.entries(live.inputs).map(([k, v]) => `<div><dt>${esc(t(`ss.in.${k}`))}</dt><dd>${esc(v == null ? "–" : fmt(v, Math.abs(v) < 10 ? 2 : 0))}</dd></div>`).join("");
  q("#ssNowCard").innerHTML = `<header class="card-head"><div><h2>${esc(t("ss.m.now"))}</h2><p class="muted">${esc(fmtTime(Date.parse(m.at)))}</p></div></header>
    <div class="ss-big"><b>${esc(fmt(live.mass_kg))}</b><small>kg</small></div>
    <p class="ss-band">p10–p90: <b>${esc(fmt(live.p10_kg))} – ${esc(fmt(live.p90_kg))} kg</b> · ${esc(t("ss.m.kgm3", { v: fmt(live.kg_m3), vol: fmt(m.machine.container_volume_m3) }))}</p>
    <ul class="ss-checks">
      <li><span class="state" data-state="info">${esc(fmt(live.ms))} ms</span>${esc(live.cached ? t("ss.m.cached") : t("ss.m.ran", { rows: fmt(live.window.rows), strokes: fmt(live.window.strokes), ms: fmt(live.ms) }))}</li>
      <li><span class="state" data-state="${same ? "good" : "warn"}">${esc(same ? t("ss.m.same") : t("ss.m.differs"))}</span>${esc(t("ss.m.batch"))}: ${esc(kg(b.mass_kg))}</li>
      <li><span class="state" data-state="note">sim</span>${esc(t("ss.m.truth"))}: ${esc(kg(m.truth_kg))} · ${esc(t("ss.m.truthErr", { err: err == null ? "–" : `${err > 0 ? "+" : ""}${fmt(err, 1)} %` }))}</li>
    </ul>
    <p class="fine">${esc(t("ss.m.model", { setup, n: live.model.n_features }))}</p>
    <h3 class="sub-h">${esc(t("ss.m.saw"))}</h3>
    <dl class="ss-inputs">${inputs}</dl>`;
}

function renderLifts(m) {
  const target = q("#ssLifts");
  if (!m.lifts.length) { target.innerHTML = `<p class="muted">${esc(t("ss.m.noLifts"))}</p>`; return; }
  const rows = [...m.lifts].reverse().map((l) => {
    const d = l.weighbridge_kg ? ((l.pred_kg - l.weighbridge_kg) / l.weighbridge_kg) * 100 : null;
    const st = d == null ? "mute" : Math.abs(d) <= 5 ? "good" : Math.abs(d) <= 10 ? "warn" : "bad";
    return `<tr><td>${esc(fmtTime(l.t))}</td><td>${esc(t(`ss.trigger.${l.trigger}`))}</td>
      <td class="num strong">${esc(kg(l.pred_kg))}<span class="desc">${esc(fmt(l.p10_kg))}–${esc(fmt(l.p90_kg))}</span></td>
      <td class="num">${esc(kg(l.weighbridge_kg))}</td>
      <td class="num"><span class="state" data-state="${st}">${d == null ? "–" : `${d > 0 ? "+" : ""}${esc(fmt(d, 1))} %`}</span></td>
      <td class="num muted-cell">${esc(kg(l.truth_kg))}</td></tr>`;
  }).join("");
  target.innerHTML = `<div class="table-wrap"><table class="table"><thead><tr><th>${esc(t("ss.col.when"))}</th><th>${esc(t("ss.col.trigger"))}</th>
    <th class="num">${esc(t("ss.col.before"))}</th><th class="num">${esc(t("ss.col.wb"))}</th><th class="num">${esc(t("ss.col.diff"))}</th>
    <th class="num">${esc(t("ss.col.truth"))}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderMachine(m) {
  if (!q("#ssMHead")) return;
  renderMachineHead(m);
  renderNow(m);
  massChart("ssMassChart", m.series, m.lifts);
  renderLifts(m);
}
