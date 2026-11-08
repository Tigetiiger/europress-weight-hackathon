// Chart.js charts for the soft-sensor pages (same defaults/palette as js/charts.js).
import { t, fmt } from "../i18n.js";
import { MAT_COLORS, MATERIALS, material, fmtDay, fmtTime, kg } from "./api.js";

/* global Chart */
const INK = "#000d61";
const GRID = "#e2e5ec";
const BAND = "rgba(0, 13, 97, .10)";
const TRUTH = "#8a8fa3";
const FILL = "#4ea3b8";
const LIFT = "#9a6a00";
const SPLIT_COLORS = { time: INK, bins: "#9aa3c7" };
const charts = new Map();

const axis = (extra = {}) => ({ grid: { color: GRID }, border: { display: false }, ...extra });

/** Create, or update in place (no animation) when the same chart type already exists. */
function upsert(id, config) {
  const canvas = document.getElementById(id);
  if (!canvas) return null;
  const old = charts.get(id);
  if (old && old.canvas === canvas && old.config.type === config.type) {
    old.data = config.data;
    old.options = config.options;
    old.update("none");
    return old;
  }
  old?.destroy();
  const c = new Chart(canvas, config);
  charts.set(id, c);
  return c;
}

export function destroyChart(id) {
  charts.get(id)?.destroy();
  charts.delete(id);
}

const xy = (ts, ys) => ts.map((x, i) => ({ x, y: ys[i] }));

/**
 * Hourly mass with the p10–p90 band, simulator truth, device fill (right axis) and weighbridge tickets.
 * `customer: true` shows only our estimate, its band and the fill: the customer gets the approximate weight,
 * never a comparison with the operator's ticket (operators are Europress's partners).
 */
export function massChart(id, s, lifts, { customer = false } = {}) {
  const lo = s.t[0], hi = s.t[s.t.length - 1];
  return upsert(id, {
    type: "line",
    data: {
      datasets: [
        { label: "p10", data: xy(s.t, s.p10_kg), borderWidth: 0, pointRadius: 0, fill: false, _hide: true },
        { label: t("ss.m.band"), data: xy(s.t, s.p90_kg), borderWidth: 0, pointRadius: 0, fill: "-1", backgroundColor: BAND },
        { label: t("ss.m.estimate"), data: xy(s.t, s.mass_kg), borderColor: INK, backgroundColor: INK, borderWidth: 2, pointRadius: 0 },
        ...(customer ? [] : [{ label: t("ss.m.truth"), data: xy(s.t, s.truth_kg), borderColor: TRUTH, backgroundColor: TRUTH, borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0 }]),
        { label: t("ss.m.fill"), data: xy(s.t, s.fill_pct), yAxisID: "y1", borderColor: FILL, backgroundColor: FILL, borderWidth: 1, pointRadius: 0, stepped: true },
        ...(customer ? [] : [{ type: "scatter", label: t("ss.m.lift"), data: lifts.map((l) => ({ x: l.t, y: l.weighbridge_kg })),
          backgroundColor: LIFT, borderColor: "#fff", borderWidth: 1.5, pointRadius: 6, pointStyle: "rectRot" }]),
      ],
    },
    options: {
      animation: false, normalized: true, spanGaps: true, interaction: { mode: "nearest", axis: "x", intersect: false },
      plugins: {
        legend: { position: "bottom", labels: { filter: (item, data) => !data.datasets[item.datasetIndex]._hide } },
        tooltip: { filter: (item) => !item.dataset._hide, callbacks: {
          title: (items) => (items.length ? fmtTime(items[0].parsed.x) : ""),
          label: (c) => `${c.dataset.label}: ${c.dataset.yAxisID === "y1" ? `${fmt(c.parsed.y)} %` : kg(c.parsed.y)}`,
        } },
      },
      scales: {
        x: axis({ type: "linear", min: lo, max: hi, grid: { display: false }, ticks: { maxTicksLimit: 8, callback: (v) => fmtDay(v) } }),
        y: axis({ min: 0, ticks: { callback: (v) => `${fmt(v / 1000, 1)} t` } }),
        y1: { position: "right", min: 0, max: 100, grid: { display: false }, border: { display: false }, ticks: { callback: (v) => `${v} %` } },
      },
    },
  });
}

/** Estimate vs weighbridge, one colour per material, with the 1:1 line. */
export function checkScatter(id, rows, onClick) {
  const max = Math.max(1000, ...rows.map((r) => Math.max(r.weighbridge_kg, r.pred_kg)));
  const datasets = MATERIALS.map((m) => ({
    label: material(m), backgroundColor: `${MAT_COLORS[m]}99`, borderWidth: 0, pointRadius: 2.5, pointHoverRadius: 5,
    data: rows.filter((r) => r.material === m).map((r) => ({ x: r.weighbridge_kg, y: r.pred_kg, r })),
  })).filter((d) => d.data.length);
  datasets.push({ type: "line", label: "1:1", data: [{ x: 0, y: 0 }, { x: max, y: max }], borderColor: GRID, borderWidth: 1.5,
    pointRadius: 0, _hide: true });
  return upsert(id, {
    type: "scatter",
    data: { datasets },
    options: {
      animation: false,
      plugins: {
        legend: { position: "bottom", labels: { filter: (item, data) => !data.datasets[item.datasetIndex]._hide } },
        tooltip: { filter: (item) => !item.dataset._hide, callbacks: { label: (c) => {
          const r = c.raw.r;
          return `${r.bin_id} · ${fmtTime(r.t)} · ${kg(r.pred_kg)} vs ${kg(r.weighbridge_kg)}`;
        } } },
      },
      scales: {
        x: axis({ min: 0, max, title: { display: true, text: t("ss.c.x") }, ticks: { callback: (v) => `${fmt(v / 1000)} t` } }),
        y: axis({ min: 0, max, title: { display: true, text: t("ss.c.y") }, ticks: { callback: (v) => `${fmt(v / 1000)} t` } }),
      },
      onClick: (_, els) => {
        const el = els.find((e) => datasets[e.datasetIndex]?.data[e.index]?.r);
        if (el) onClick?.(datasets[el.datasetIndex].data[el.index].r);
      },
    },
  });
}

/** Histogram of signed % differences, clipped to ±limit. */
export function errorHistogram(id, diffs, limit = 15) {
  const labels = [];
  const counts = [];
  for (let b = -limit; b < limit; b += 1) { labels.push(b); counts.push(0); }
  for (const d of diffs) {
    const b = Math.min(limit - 1, Math.max(-limit, Math.floor(d)));
    counts[b + limit] += 1;
  }
  return upsert(id, {
    type: "bar",
    data: { labels: labels.map((b) => `${b > 0 ? "+" : ""}${b}`), datasets: [{
      data: counts, backgroundColor: labels.map((b) => (Math.abs(b + 0.5) <= 5 ? INK : "#b9bfd6")), borderRadius: 2, barPercentage: 1, categoryPercentage: .92,
    }] },
    options: {
      animation: false, plugins: { legend: { display: false }, tooltip: { callbacks: {
        title: (items) => `${items[0].label} … ${Number(items[0].label) + 1} %`, label: (c) => `${fmt(c.raw)} ${t("ss.c.n")}`,
      } } },
      scales: { x: axis({ grid: { display: false }, ticks: { maxTicksLimit: 11 } }), y: axis({ ticks: { precision: 0 } }) },
    },
  });
}

/** Ladder: mean % error per step, pilot fleet vs new installations. */
export function ladderChart(id, splits, steps) {
  const datasets = Object.entries(splits).map(([split, s]) => ({
    label: t(`ss.split.${split}`), backgroundColor: SPLIT_COLORS[split], borderRadius: 4, maxBarThickness: 16,
    data: steps.map((step) => s.ladder[step]?.all?.mean_pct ?? null),
  }));
  return upsert(id, {
    type: "bar",
    data: { labels: steps.map((s) => t(`ss.step.${s}`)), datasets },
    options: {
      indexAxis: "y", animation: false,
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw, 1)} %` } } },
      scales: { x: axis({ min: 0, ticks: { callback: (v) => `${v} %` } }), y: axis({ grid: { display: false } }) },
    },
  });
}

export function learningChart(id, curve) {
  const keys = ["all", ...MATERIALS].filter((k) => curve[k]?.length);
  return upsert(id, {
    type: "line",
    data: { datasets: keys.map((k) => ({
      label: material(k), data: curve[k].map(([x, y]) => ({ x, y })),
      borderColor: k === "all" ? INK : MAT_COLORS[k], backgroundColor: k === "all" ? INK : MAT_COLORS[k],
      borderWidth: k === "all" ? 3 : 1.5, pointRadius: 3, tension: .25,
    })) },
    options: {
      animation: false,
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.parsed.y, 1)} %` } } },
      scales: {
        x: axis({ type: "logarithmic", title: { display: true, text: t("ss.mo.learn.x") }, ticks: { callback: (v) => ([10, 25, 50, 100, 300].includes(v) ? v : "") } }),
        y: axis({ min: 0, ticks: { callback: (v) => `${v} %` } }),
      },
    },
  });
}

export function importanceChart(id, pairs, labelOf, unit) {
  return upsert(id, {
    type: "bar",
    data: { labels: pairs.map(([n]) => labelOf(n)), datasets: [{ data: pairs.map(([, v]) => v), backgroundColor: INK, borderRadius: 4, maxBarThickness: 14 }] },
    options: {
      indexAxis: "y", animation: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => (unit === "%" ? `${fmt(c.raw * 100, 1)} %` : `${fmt(c.raw, 2)} kg/m³`) } } },
      scales: { x: axis({ ticks: { callback: (v) => (unit === "%" ? `${fmt(v * 100)} %` : fmt(v, 1)) } }), y: axis({ grid: { display: false } }) },
    },
  });
}

export function curvesChart(id, curves) {
  const shades = ["#c3cad9", "#9aa3c7", "#6b78b0", "#3a4790", INK];
  return upsert(id, {
    type: "line",
    data: { datasets: curves.map((c, i) => ({
      label: t("ss.h.curves.fill", { pct: fmt(c.fill_pct) }), data: c.position.map((x, k) => ({ x, y: c.pressure_bar[k] })),
      borderColor: shades[i % shades.length], backgroundColor: shades[i % shades.length], borderWidth: 2, pointRadius: 0, tension: .3,
    })) },
    options: {
      animation: false,
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.parsed.y, 1)} bar` } } },
      scales: {
        x: axis({ type: "linear", min: 0, max: 1, title: { display: true, text: t("ss.h.curves.x") } }),
        y: axis({ min: 0, title: { display: true, text: t("ss.h.curves.y") } }),
      },
    },
  });
}
