// Model evaluation: pilot fleet (time split) vs new installations (bins split), ladder, learning curve, drivers.
import { t, fmt, addStrings } from "../i18n.js";
import { esc } from "../drawer.js";
import { sensorStatic, material, MATERIALS, pct } from "./api.js";
import { ladderChart, learningChart, importanceChart } from "./charts.js";

const LADDER = ["naive_fill_x_density", "fill_presses", "pressure", "pressure_graph", "full_shared", "full_per_material", "full_selected"];
const TOP_DRIVERS = 12;
const COVERAGE_LOW = 0.7;
const state = { mat: "cardboard", source: "importance" };

addStrings({
  en: {
    "ss.f.curve_rise_slope_ewm": "Pressure graph: rise slope (smoothed)", "ss.f.presses_per_fill": "Presses per fill %",
    "ss.f.container_volume_m3": "Container volume", "ss.f.infeed": "Infeed type", "ss.f.p_last": "Last peak pressure",
    "ss.f.curve_mean_bar_ewm": "Pressure graph: mean pressure", "ss.f.rh_mean_cycle": "Humidity this cycle", "ss.f.graph_kg_m3": "Pressure-graph density",
    "ss.f.rho_p_smooth": "Pressure-implied density", "ss.f.rho_p": "Pressure-implied density (raw)", "ss.f.p_count_cycle": "Strokes with pressure this cycle",
    "ss.f.drainage": "Drainage", "ss.f.fill_max_cycle": "Highest fill this cycle", "ss.f.fill_change_24h": "Fill change, 24 h",
    "ss.f.equipment_class": "Equipment class", "ss.f.curve_contact_min_cycle": "Pressure graph: earliest contact this cycle",
    "ss.f.rh_prev_day": "Humidity yesterday", "ss.f.stroke_duration_mean": "Stroke duration", "ss.f.stroke_peak_mean": "Stroke peak pressure",
    "ss.f.energy_kwh": "Energy", "ss.f.presses": "Presses this hour", "ss.f.p_now": "Peak pressure this hour", "ss.f.hour": "Hour of day", "ss.f.weekday": "Weekday",
  },
  fi: {
    "ss.f.curve_rise_slope_ewm": "Painekäyrä: nousun jyrkkyys (tasoitettu)", "ss.f.presses_per_fill": "Puristuksia täyttöprosenttia kohden",
    "ss.f.container_volume_m3": "Säiliön tilavuus", "ss.f.infeed": "Syöttötapa", "ss.f.p_last": "Viimeisin huippupaine",
    "ss.f.curve_mean_bar_ewm": "Painekäyrä: keskipaine", "ss.f.rh_mean_cycle": "Kosteus jakson aikana", "ss.f.graph_kg_m3": "Painekäyrästä päätelty tiheys",
    "ss.f.rho_p_smooth": "Paineesta päätelty tiheys", "ss.f.rho_p": "Paineesta päätelty tiheys (raaka)", "ss.f.p_count_cycle": "Paineellisia puristuksia jaksossa",
    "ss.f.drainage": "Kuivatus", "ss.f.fill_max_cycle": "Jakson suurin täyttö", "ss.f.fill_change_24h": "Täytön muutos 24 h",
    "ss.f.equipment_class": "Laitetyyppi", "ss.f.curve_contact_min_cycle": "Painekäyrä: jakson aikaisin kosketus",
    "ss.f.rh_prev_day": "Eilinen kosteus", "ss.f.stroke_duration_mean": "Puristuksen kesto", "ss.f.stroke_peak_mean": "Puristuksen huippupaine",
    "ss.f.energy_kwh": "Energia", "ss.f.presses": "Puristuksia tunnissa", "ss.f.p_now": "Huippupaine tällä tunnilla", "ss.f.hour": "Kellonaika", "ss.f.weekday": "Viikonpäivä",
  },
});

export function featureLabel(name) {
  for (const key of [`ss.in.${name}`, `ss.f.${name}`]) {
    const s = t(key);
    if (s !== key) return s;
  }
  if (name.startsWith("rain_mm_d")) return `${t("ss.in.rain_72h").split(",")[0]} d-${name.slice(-1)}`;
  return name;
}

const share = (v) => (v == null ? "–" : `${fmt(v * 100)} %`);

function headlineTable(e) {
  const s = e.splits;
  const naive = (k) => s[k]?.ladder?.naive_fill_x_density?.all?.mean_pct;
  const rows = [
    ["ss.mo.liftErr", (k) => pct(s[k].lift.overall.mean_pct, 2), true],
    ["ss.mo.liftMed", (k) => pct(s[k].lift.overall.median_pct, 2)],
    ["ss.mo.w5", (k) => share(s[k].lift.overall.within_5pct)],
    ["ss.mo.w10", (k) => share(s[k].lift.overall.within_10pct)],
    ["ss.mo.cov", (k) => share(s[k].lift.overall.coverage)],
    ["ss.mo.hourMae", (k) => `${fmt(s[k].hourly.overall.mae_kg)} kg`],
    ["ss.mo.machines", (k) => `${fmt(s[k].n_train_machines)} / ${fmt(s[k].n_test_machines)}`],
    ["ss.mo.naive", (k) => pct(naive(k), 1)],
  ];
  const splits = ["time", "bins"].filter((k) => s[k]);
  return `<div class="table-wrap"><table class="table ss-compare"><thead><tr><th>${esc(t("ss.mo.metric"))}</th>${
    splits.map((k) => `<th class="num">${esc(t(`ss.split.${k}`))}<span class="desc">${esc(t(`ss.split.${k}.d`))}</span></th>`).join("")}</tr></thead>
    <tbody>${rows.map(([key, f, strong]) => `<tr><td>${esc(t(key))}</td>${splits.map((k) => `<td class="num${strong ? " strong" : ""}">${esc(f(k))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function materialTable(e) {
  const s = e.splits;
  const cov = (v) => `<span class="state" data-state="${v < COVERAGE_LOW ? "bad" : "good"}">${esc(share(v))}</span>`;
  const cell = (k, m) => {
    const x = s[k]?.lift.by_material[m];
    return x ? `<td class="num strong">${esc(pct(x.mean_pct))}</td><td class="num">${esc(share(x.within_5pct))}</td><td class="num">${cov(x.coverage)}</td>` : "<td></td><td></td><td></td>";
  };
  const head = (k) => `<th class="num">${esc(t(`ss.split.${k}`))}: ${esc(t("ss.mo.liftErr").toLowerCase())}</th><th class="num">±5 %</th><th class="num">p10–p90</th>`;
  return `<div class="table-wrap"><table class="table"><thead><tr><th>${esc(t("ss.col.material"))}</th>${head("time")}${head("bins")}</tr></thead>
    <tbody>${MATERIALS.map((m) => `<tr><td class="strong">${esc(material(m))}</td>${cell("time", m)}${cell("bins", m)}</tr>`).join("")}</tbody></table></div>`;
}

function computeFacts(c) {
  const facts = [
    [t("ss.mo.train"), `${fmt(c.train_wall_s / 60, 1)} min`],
    [t("ss.mo.infer"), t("ss.mo.infer.v", { us: fmt(c.predict_us_per_machine_hour, 1) })],
    [t("ss.mo.rows"), t("ss.mo.rows.v", { rows: fmt(c.rows), m: fmt(c.machines) })],
    [t("ss.mo.ram"), `${fmt(c.peak_rss_gb, 1)} GB`],
  ];
  return `<dl class="stats ss-stats">${facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>`;
}

function drawDrivers(root, e) {
  const split = e.splits.time;
  const pairs = (state.source === "shap" ? split.shap : split.importance)[state.mat] || [];
  importanceChart("moImp", pairs.slice(0, TOP_DRIVERS), featureLabel, state.source === "shap" ? "kg/m³" : "%");
  root.querySelectorAll("#moImpMat button, #moImpSrc button").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.mat === state.mat || b.dataset.src === state.source)));
}

export async function renderModel(root) {
  root.innerHTML = `<p class="note">${esc(t("ss.loading"))}</p>`;
  let e;
  try { e = await sensorStatic("evaluation"); } catch (err) { root.innerHTML = `<p class="note">${esc(err.message)}</p>`; return; }
  const mats = MATERIALS.map((m) => `<button type="button" data-mat="${m}">${esc(material(m))}</button>`).join("");
  root.innerHTML = `
    <article class="card"><header class="card-head"><h2>${esc(t("ss.mo.head"))}</h2></header>${headlineTable(e)}${computeFacts(e.compute)}</article>
    <article class="card"><header class="card-head"><h2>${esc(t("ss.mo.byMat"))}</h2></header>${materialTable(e)}
      <p class="note" data-state="warn">${esc(t("ss.c.mixedCaveat"))}</p></article>
    <div class="grid g-1-1">
      <article class="card"><header class="card-head"><div><h2>${esc(t("ss.mo.ladder"))}</h2><p class="muted">${esc(t("ss.mo.ladder.sub"))}</p></div></header><div class="chart ss-chart-tall"><canvas id="moLadder"></canvas></div></article>
      <article class="card"><header class="card-head"><div><h2>${esc(t("ss.mo.learn"))}</h2><p class="muted">${esc(t("ss.mo.learn.sub"))}</p></div></header><div class="chart ss-chart-tall"><canvas id="moLearn"></canvas></div></article>
    </div>
    <article class="card"><header class="card-head"><div><h2>${esc(t("ss.mo.imp"))}</h2><p class="muted">${esc(t("ss.mo.imp.sub"))}</p></div>
      <div class="head-actions"><div class="seg ss-wrap" id="moImpMat">${mats}</div><div class="seg" id="moImpSrc"><button type="button" data-src="importance">${esc(t("ss.mo.imp.gain"))}</button><button type="button" data-src="shap">${esc(t("ss.mo.imp.shap"))}</button></div></div></header>
      <div class="chart ss-chart-tall"><canvas id="moImp"></canvas></div></article>`;
  ladderChart("moLadder", e.splits, LADDER);
  learningChart("moLearn", e.learning_curve);
  root.querySelector("#moImpMat").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-mat]");
    if (b) { state.mat = b.dataset.mat; drawDrivers(root, e); }
  });
  root.querySelector("#moImpSrc").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-src]");
    if (b) { state.source = b.dataset.src; drawDrivers(root, e); }
  });
  drawDrivers(root, e);
}
