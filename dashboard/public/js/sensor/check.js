// Weighbridge check: estimate one hour before every test-period emptying vs the transporter's ticket.
import { t, fmt } from "../i18n.js";
import { esc } from "../drawer.js";
import { sensorStatic, fmtTime, kg, material, MATERIALS, MAT_COLORS } from "./api.js";
import { checkScatter, errorHistogram } from "./charts.js";
import { openMachineAt } from "./live.js";

const TOP_N = 12;
const state = { split: "time", filter: "all" };

const toObjects = (s) => s.rows.map((r) => Object.fromEntries(s.columns.map((c, i) => [c, r[i]])));
const diffPct = (r) => ((r.pred_kg - r.weighbridge_kg) / r.weighbridge_kg) * 100;
const mean = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);

function summary(rows) {
  const d = rows.map(diffPct);
  return {
    n: rows.length,
    meanAbs: mean(d.map(Math.abs)),
    within5: rows.length ? d.filter((x) => Math.abs(x) <= 5).length / rows.length : null,
    coverage: rows.length ? rows.filter((r) => r.weighbridge_kg >= r.p10_kg && r.weighbridge_kg <= r.p90_kg).length / rows.length : null,
  };
}

function controls() {
  const splits = ["time", "bins"].map((s) => `<button type="button" data-split="${s}" aria-pressed="${s === state.split}">${esc(t(`ss.split.${s}`))}</button>`).join("");
  const mats = ["all", ...MATERIALS].map((m) => `<button type="button" data-mat="${m}" aria-pressed="${m === state.filter}">${esc(material(m))}</button>`).join("");
  return `<div class="ss-filters">
    <div class="seg" role="group" aria-label="${esc(t("ss.split"))}" id="ckSplit">${splits}</div>
    <div class="seg ss-wrap" role="group" aria-label="${esc(t("ss.col.material"))}" id="ckMat">${mats}</div>
  </div><p class="muted" id="ckSplitDesc"></p>`;
}

const kpi = (label, value, sub) => `<article class="kpi"><div class="kpi-label">${esc(label)}</div><div class="kpi-value">${esc(value)}</div><div class="kpi-sub">${esc(sub)}</div></article>`;

const largest = (rows) => [...rows].sort((a, b) => Math.abs(diffPct(b)) - Math.abs(diffPct(a))).slice(0, TOP_N);

function topTable(top) {
  const linkable = state.split === "time";
  return `<div class="table-wrap"><table class="table"><thead><tr><th>${esc(t("ss.col.machine"))}</th><th>${esc(t("ss.col.when"))}</th>
    <th class="num">${esc(t("ss.col.before"))}</th><th class="num">${esc(t("ss.col.wb"))}</th><th class="num">${esc(t("ss.col.diff"))}</th><th class="num">${esc(t("ss.col.truth"))}</th></tr></thead>
    <tbody>${top.map((r, i) => {
      const d = diffPct(r);
      return `<tr${linkable ? ` class="click" tabindex="0" data-i="${i}"` : ""}>
        <td class="strong"><span class="dot" style="background:${MAT_COLORS[r.material]}"></span>${esc(r.bin_id)}<span class="desc">${esc(material(r.material))} · ${fmt(r.volume_m3)} m³${r.false_full ? ` · ${esc(t("ss.c.falseFull"))}` : ""}</span></td>
        <td>${esc(fmtTime(r.t))}</td><td class="num strong">${esc(kg(r.pred_kg))}<span class="desc">${esc(fmt(r.p10_kg))}–${esc(fmt(r.p90_kg))}</span></td>
        <td class="num">${esc(kg(r.weighbridge_kg))}</td>
        <td class="num"><span class="state" data-state="${Math.abs(d) > 10 ? "bad" : "warn"}">${d > 0 ? "+" : ""}${esc(fmt(d, 1))} %</span></td>
        <td class="num muted-cell">${esc(kg(r.truth_kg))}</td></tr>`;
    }).join("")}</tbody></table></div>`;
}

function render(root, data) {
  const all = toObjects(data.splits[state.split]);
  const rows = state.filter === "all" ? all : all.filter((r) => r.material === state.filter);
  const s = summary(rows);
  root.querySelector("#ckSplitDesc").textContent = t(`ss.split.${state.split}.d`);
  root.querySelector("#ckCaveat").classList.toggle("hidden", !(state.split === "bins" && ["all", "mixed_waste"].includes(state.filter)));
  root.querySelector("#ckKpis").innerHTML = [
    kpi(t("ss.c.lifts"), fmt(s.n), t("ss.c.lifts.sub")),
    kpi(t("ss.c.err"), `${fmt(s.meanAbs, 1)} %`, t("ss.c.err.sub")),
    kpi(t("ss.c.w5"), `${fmt(s.within5 * 100)} %`, t("ss.c.w5.sub")),
    kpi(t("ss.c.cov"), `${fmt(s.coverage * 100)} %`, t("ss.c.cov.sub")),
  ].join("");
  const open = (r) => { if (state.split === "time") openMachineAt(r.bin_id, r.t - 3_600_000); };
  checkScatter("ckScatter", rows, open);
  errorHistogram("ckHist", rows.map(diffPct));
  const top = root.querySelector("#ckTop");
  const topRows = largest(rows);
  top.innerHTML = topTable(topRows);
  top.querySelectorAll("tr[data-i]").forEach((tr) => {
    const go = () => open(topRows[Number(tr.dataset.i)]);
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
  });
}

export async function renderCheck(root) {
  root.innerHTML = `<p class="note">${esc(t("ss.loading"))}</p>`;
  let data;
  try { data = await sensorStatic("check"); } catch (e) { root.innerHTML = `<p class="note">${esc(e.message)}</p>`; return; }
  root.innerHTML = `<p class="lead">${esc(t("ss.c.why"))}</p>${controls()}
    <p class="note hidden" id="ckCaveat" data-state="warn">${esc(t("ss.c.mixedCaveat"))}</p>
    <div class="kpis" id="ckKpis"></div>
    <div class="grid g-1-1">
      <article class="card"><header class="card-head"><h2>${esc(t("ss.c.scatter"))}</h2></header><div class="chart ss-chart-tall"><canvas id="ckScatter"></canvas></div></article>
      <article class="card"><header class="card-head"><h2>${esc(t("ss.c.hist"))}</h2></header><div class="chart ss-chart-tall"><canvas id="ckHist"></canvas></div></article>
    </div>
    <article class="card"><header class="card-head"><div><h2>${esc(t("ss.c.top"))}</h2><p class="muted">${esc(t("ss.c.top.sub"))}</p></div></header><div id="ckTop"></div></article>`;
  const bindSeg = (id, key, attr) => root.querySelector(id).addEventListener("click", (e) => {
    const b = e.target.closest(`button[data-${attr}]`);
    if (!b) return;
    state[key] = b.dataset[attr];
    root.querySelector(id).querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    render(root, data);
  });
  bindSeg("#ckSplit", "split", "split");
  bindSeg("#ckMat", "filter", "mat");
  render(root, data);
}
