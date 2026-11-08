// How it works: signal flow, pressure graphs, the simulated fleet, validation plots and caveats.
import { t, fmt } from "../i18n.js";
import { esc } from "../drawer.js";
import { sensorStatic, material, MATERIALS } from "./api.js";
import { curvesChart } from "./charts.js";

const CAVEATS = ["ss.h.l6", "ss.h.l1", "ss.h.l2", "ss.h.l3", "ss.h.l4", "ss.h.l5"];

function flow(nFeatures) {
  const steps = [["ss.h.f1", "ss.h.f1.d"], ["ss.h.f2", "ss.h.f2.d"], ["ss.h.f3", "ss.h.f3.d"], ["ss.h.f4", "ss.h.f4.d"], ["ss.h.f5", "ss.h.f5.d"]];
  return `<ol class="ss-flow">${steps.map(([h, d], i) => `<li><span class="ss-flow-n">${i + 1}</span><b>${esc(t(h))}</b><p>${esc(t(d, { n: nFeatures }))}</p></li>`).join("")}</ol>`;
}

function simFacts(sim) {
  const facts = [
    [t("ss.h.sim.machines"), fmt(sim.machines)], [t("ss.h.sim.days"), fmt(sim.days)], [t("ss.h.sim.rows"), fmt(sim.hourly_rows)],
    [t("ss.h.sim.lifts"), fmt(sim.lifts)], [t("ss.h.sim.ff"), `${fmt(sim.false_full_share * 100, 1)} %`],
  ];
  const mass = MATERIALS.filter((m) => sim.lift_mass_by_material[m]).map((m) =>
    `<tr><td>${esc(material(m))}</td><td class="num">${fmt(sim.fleet.material?.[m] ?? null)}</td><td class="num">${fmt(sim.lift_mass_by_material[m].count)}</td><td class="num strong">${fmt(sim.lift_mass_by_material[m].mean)} kg</td></tr>`).join("");
  return `<dl class="stats ss-stats">${facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
    <div class="table-wrap"><table class="table"><thead><tr><th>${esc(t("ss.col.material"))}</th><th class="num">${esc(t("ss.h.sim.machines"))}</th>
    <th class="num">${esc(t("ss.h.sim.lifts"))}</th><th class="num">${esc(t("ss.h.sim.mean"))}</th></tr></thead><tbody>${mass}</tbody></table></div>`;
}

function gallery(plots) {
  return `<div class="ss-gallery">${plots.map((src) => {
    const key = src.split("/").pop().replace(".png", "");
    return `<figure><a href="${esc(src)}" target="_blank" rel="noopener"><img src="${esc(src)}" alt="${esc(t(`ss.plot.${key}`))}" loading="lazy"></a><figcaption>${esc(t(`ss.plot.${key}`))}</figcaption></figure>`;
  }).join("")}</div>`;
}

export async function renderHow(root) {
  root.innerHTML = `<p class="note">${esc(t("ss.loading"))}</p>`;
  let x;
  try { x = await sensorStatic("explainer"); } catch (e) { root.innerHTML = `<p class="note">${esc(e.message)}</p>`; return; }
  root.innerHTML = `
    <article class="card"><header class="card-head"><h2>${esc(t("ss.h.flow"))}</h2></header>${flow(x.model.n_features)}</article>
    <div class="grid g-1-1">
      <article class="card"><header class="card-head"><div><h2>${esc(t("ss.h.curves"))}</h2><p class="muted">${esc(t("ss.h.curves.sub", { machine: x.pressure_curves.machine }))}</p></div></header>
        <div class="chart ss-chart-tall"><canvas id="howCurves"></canvas></div></article>
      <article class="card"><header class="card-head"><h2>${esc(t("ss.h.sim"))}</h2></header>${simFacts(x.simulation)}</article>
    </div>
    <article class="card"><header class="card-head"><h2>${esc(t("ss.h.limits"))}</h2></header><ul class="ss-caveats">${CAVEATS.map((k) => `<li>${esc(t(k))}</li>`).join("")}</ul></article>
    <article class="card"><header class="card-head"><h2>${esc(t("ss.h.plots"))}</h2></header>${gallery(x.plots)}</article>`;
  curvesChart("howCurves", x.pressure_curves.curves);
}
