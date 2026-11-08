// Chart.js wrappers in the Europress palette. Chart is loaded globally from /vendor/chart.umd.js.
import { t, stream, fmt, eur, month } from "./i18n.js";

/* global Chart */
// Categorical hues for waste streams (data, not state). Mixed waste is the dark neutral: "less of it" is the goal.
export const STREAM_COLORS = {
  mixed_waste: "#3a3f52", cardboard: "#b8894a", paper: "#5b7bb4", plastic_film: "#4ea3b8",
  mixed_plastic: "#7a68b5", biowaste: "#4f9a6a",
};
const INK = "#000d61";
const GRID = "#e2e5ec";
const STATE = { good: "#1a7a47", warn: "#9a6a00", bad: "#b43c3c", mute: "#c9cdd8", track: "#eceef3" };
const charts = new Map();

Chart.defaults.font.family = '"Geist Mono", ui-monospace, monospace';
Chart.defaults.font.size = 11;
Chart.defaults.color = "#4a5170";
Chart.defaults.plugins.legend.labels.boxWidth = 10;
Chart.defaults.plugins.legend.labels.boxHeight = 10;
Chart.defaults.plugins.legend.labels.usePointStyle = true;
Chart.defaults.plugins.tooltip.backgroundColor = INK;
Chart.defaults.plugins.tooltip.padding = 10;
Chart.defaults.plugins.tooltip.cornerRadius = 0;
Chart.defaults.maintainAspectRatio = false;
Chart.defaults.animation.duration = 450;

function draw(id, config) {
  charts.get(id)?.destroy();
  const el = document.getElementById(id);
  if (!el) return null;
  const c = new Chart(el, config);
  charts.set(id, c);
  return c;
}

const axis = (extra = {}) => ({ grid: { color: GRID }, border: { display: false }, ...extra });

/** Stacked monthly cost by stream; onClick(streamKey) opens provenance. */
export function costChart(p, onClick) {
  const labels = p.period.months.map(month);
  const datasets = Object.entries(p.monthly.cost_by_stream).map(([s, v]) => ({
    label: stream(s), data: v, backgroundColor: STREAM_COLORS[s], borderRadius: 4, borderSkipped: false, maxBarThickness: 22, _s: s,
  }));
  return draw("costChart", {
    type: "bar",
    data: { labels, datasets },
    options: {
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${eur(c.raw)}` } } },
      scales: { x: axis({ stacked: true, grid: { display: false } }), y: axis({ stacked: true, ticks: { callback: (v) => eur(v) } }) },
      onClick: (_, els) => els.length && onClick?.(datasets[els[0].datasetIndex]._s),
    },
  });
}

export function recyclingChart(p) {
  const labels = p.period.months.map(month);
  return draw("recChart", {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: t("series.recycling"), data: p.monthly.recycling_rate, borderColor: STATE.good, backgroundColor: STATE.good, tension: .35, pointRadius: 2, borderWidth: 2 },
        { label: t("series.mixed"), data: p.monthly.mixed_share, borderColor: STREAM_COLORS.mixed_waste, backgroundColor: STREAM_COLORS.mixed_waste, borderDash: [5, 4], tension: .35, pointRadius: 2, borderWidth: 2 },
      ],
    },
    options: {
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw, 1)} %` } } },
      scales: { x: axis({ grid: { display: false } }), y: axis({ min: 0, max: 100, ticks: { callback: (v) => `${v} %` } }) },
    },
  });
}

export function weightChart(p) {
  const w = p.weight;
  const data = [w.verified_within_10pct, w.difference_gt_10pct, w.device_only, w.other];
  return draw("weightChart", {
    type: "doughnut",
    data: {
      labels: [t("w.verified"), t("w.differ"), t("w.device"), t("w.other")],
      datasets: [{ data, backgroundColor: [STATE.good, "#d9b36a", INK, STATE.mute], borderWidth: 0, spacing: 2 }],
    },
    options: { cutout: "72%", plugins: { legend: { position: "right" } } },
  });
}

export function densityChart(p) {
  const labels = p.period.months.map(month);
  const datasets = Object.entries(p.monthly.density_by_stream).map(([s, v]) => ({
    label: stream(s), data: v, borderColor: STREAM_COLORS[s], backgroundColor: STREAM_COLORS[s], tension: .35, pointRadius: 2, borderWidth: 2, spanGaps: true,
  }));
  return draw("densityChart", {
    type: "line",
    data: { labels, datasets },
    options: {
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw)} kg/m³` } } },
      scales: { x: axis({ grid: { display: false } }), y: axis({ ticks: { callback: (v) => `${v}` } }) },
    },
  });
}

export function opStreamChart(o) {
  const entries = Object.entries(o.forecast_by_stream_t);
  return draw("opStreamChart", {
    type: "bar",
    data: {
      labels: entries.map(([s]) => stream(s)),
      datasets: [{ data: entries.map(([, v]) => v), backgroundColor: entries.map(([s]) => STREAM_COLORS[s]), borderRadius: 4, maxBarThickness: 20 }],
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${fmt(c.raw, 1)} t` } } },
      scales: { x: axis({ ticks: { callback: (v) => `${v} t` } }), y: axis({ grid: { display: false } }) },
    },
  });
}

export function histChart(id, hist) {
  const labels = hist.map((_, i) => `${i * 10}–${i * 10 + 10} %`);
  return draw(id, {
    type: "bar",
    data: {
      labels,
      datasets: [{ data: hist, backgroundColor: hist.map((_, i) => (i < 6 ? "#d9b36a" : i < 8 ? STATE.mute : STATE.good)), borderRadius: 4 }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: { x: axis({ grid: { display: false } }), y: axis() },
    },
  });
}

/** Semi-circle gauge (Figma "Overall progress") as inline SVG. */
export function gaugeSvg(value, target) {
  const v = Math.max(0, Math.min(100, value || 0));
  const R = 120, cx = 150, cy = 150;
  const pt = (pct, r = R) => {
    const a = Math.PI * (1 - pct / 100);
    return [cx + r * Math.cos(a), cy - r * Math.sin(a)];
  };
  const arc = (from, to, r = R) => {
    const [x1, y1] = pt(from, r), [x2, y2] = pt(to, r);
    return `M${x1.toFixed(1)} ${y1.toFixed(1)} A${r} ${r} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)}`;
  };
  const ticks = Array.from({ length: 41 }, (_, i) => {
    const [x1, y1] = pt(i * 2.5, R + 14), [x2, y2] = pt(i * 2.5, R + (i % 4 === 0 ? 24 : 20));
    return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${STATE.mute}" stroke-width="1"/>`;
  }).join("");
  const [tx, ty] = pt(target, R);
  const color = v >= target - 5 ? STATE.good : v >= 60 ? STATE.warn : STATE.bad;
  return `<svg viewBox="0 0 300 175" aria-hidden="true">
    ${ticks}
    <path d="${arc(0, 100)}" stroke="${STATE.track}" stroke-width="14" fill="none" stroke-linecap="round"/>
    <path d="${arc(0.01, Math.max(0.5, v))}" stroke="${color}" stroke-width="14" fill="none" stroke-linecap="round"/>
    <circle cx="${tx.toFixed(1)}" cy="${ty.toFixed(1)}" r="6" fill="#fff" stroke="${INK}" stroke-width="2.5"/>
    <text x="22" y="172" font-size="11" fill="#868ba0">0</text><text x="266" y="172" font-size="11" fill="#868ba0">100</text>
  </svg>`;
}

// ---------------------------------------------------------------- Europress + processor charts

export function flowChart(id, months, byStream) {
  const datasets = Object.entries(byStream).map(([s, v]) => ({
    label: stream(s), data: v, backgroundColor: STREAM_COLORS[s], borderRadius: 3, maxBarThickness: 22,
  }));
  return draw(id, {
    type: "bar", data: { labels: months.map(month), datasets },
    options: {
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw)} t` } } },
      scales: { x: axis({ stacked: true, grid: { display: false } }), y: axis({ stacked: true, ticks: { callback: (v) => `${fmt(v)} t` } }) },
    },
  });
}

export function barChart(id, labels, values, unit = "", horizontal = false) {
  return draw(id, {
    type: "bar",
    data: { labels, datasets: [{ data: values, backgroundColor: INK, borderRadius: 4, maxBarThickness: 22 }] },
    options: {
      indexAxis: horizontal ? "y" : "x",
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${fmt(c.raw)} ${unit}` } } },
      scales: { x: axis({ grid: { display: !horizontal ? false : true } }), y: axis({ grid: { display: horizontal ? false : true } }) },
    },
  });
}

export function gradeChart(id, byStream) {
  const streams = Object.keys(byStream);
  // plants do not grade loads: accepted (recycled) or rejected (burned)
  const datasets = ["accepted", "rejected"].map((g) => ({
    label: t(`q.${g}`), backgroundColor: g === "accepted" ? STATE.good : STATE.bad, borderRadius: 2, maxBarThickness: 26,
    data: streams.map((s) => { const row = byStream[s]; const tot = Object.values(row).reduce((a, b) => a + b, 0); return tot ? ((row[g] || 0) / tot) * 100 : 0; }),
  }));
  return draw(id, {
    type: "bar", data: { labels: streams.map(stream), datasets },
    options: {
      indexAxis: "y",
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw, 1)} %` } } },
      scales: { x: axis({ stacked: true, max: 100, ticks: { callback: (v) => `${v} %` } }), y: axis({ stacked: true, grid: { display: false } }) },
    },
  });
}

export function priceChart(id, months, index) {
  const datasets = Object.entries(index).map(([m, v]) => ({
    label: stream(m), data: v, borderColor: STREAM_COLORS[m], backgroundColor: STREAM_COLORS[m], tension: .3, pointRadius: 2, borderWidth: 2,
  }));
  return draw(id, {
    type: "line", data: { labels: months.map(month), datasets },
    options: {
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw, 1)} €/t` } } },
      scales: { x: axis({ grid: { display: false } }), y: axis({ ticks: { callback: (v) => `${v} €/t` } }) },
    },
  });
}

export function regionChart(id, byRegion) {
  const regions = Object.keys(byRegion);
  const streams = [...new Set(regions.flatMap((r) => Object.keys(byRegion[r])))];
  const datasets = streams.map((s) => ({
    label: stream(s), backgroundColor: STREAM_COLORS[s], borderRadius: 2, maxBarThickness: 30, data: regions.map((r) => byRegion[r][s] || 0),
  }));
  return draw(id, {
    type: "bar", data: { labels: regions, datasets },
    options: {
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw, 1)} t` } } },
      scales: { x: axis({ stacked: true, grid: { display: false } }), y: axis({ stacked: true, ticks: { callback: (v) => `${v} t` } }) },
    },
  });
}

// ---------------------------------------------------------------- trends (Trends tab)
const LINK = "#1863dc";
const dual = (left, right) => ({ x: axis({ grid: { display: false } }), y: axis(left), y1: axis({ position: "right", grid: { display: false }, ...right }) });

export function mixedTrendChart(p) {
  const labels = p.period.months.map(month);
  const tons = p.monthly.tonnes_by_stream.mixed_waste || labels.map(() => 0);
  return draw("mixedTrendChart", {
    type: "bar",
    data: { labels, datasets: [
      { type: "bar", label: t("tr.mixedTonnes"), data: tons, backgroundColor: STREAM_COLORS.mixed_waste, borderRadius: 3, maxBarThickness: 22, yAxisID: "y" },
      { type: "line", label: t("series.mixed"), data: p.monthly.mixed_share, borderColor: STATE.bad, backgroundColor: STATE.bad, tension: .35, pointRadius: 2, borderWidth: 2, yAxisID: "y1" },
    ] },
    options: { plugins: { legend: { position: "bottom" } }, scales: dual({ ticks: { callback: (v) => `${v} t` } }, { min: 0, max: 100, ticks: { callback: (v) => `${v} %` } }) },
  });
}

export function streamTrendChart(p) {
  const labels = p.period.months.map(month);
  const datasets = Object.entries(p.monthly.tonnes_by_stream).map(([s, v]) => ({
    label: stream(s), data: v, backgroundColor: STREAM_COLORS[s], borderRadius: 3, maxBarThickness: 22,
  }));
  return draw("streamTrendChart", {
    type: "bar", data: { labels, datasets },
    options: { plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw, 1)} t` } } },
      scales: { x: axis({ stacked: true, grid: { display: false } }), y: axis({ stacked: true, ticks: { callback: (v) => `${v} t` } }) } },
  });
}

export function costPickupChart(p) {
  const labels = p.period.months.map(month);
  const cost = labels.map((_, i) => Object.values(p.monthly.cost_by_stream).reduce((a, v) => a + (v[i] || 0), 0));
  const tons = labels.map((_, i) => Object.values(p.monthly.tonnes_by_stream).reduce((a, v) => a + (v[i] || 0), 0));
  return draw("costPickupChart", {
    type: "bar",
    data: { labels, datasets: [
      { type: "bar", label: t("tr.pickups"), data: p.monthly.pickups, backgroundColor: STATE.mute, borderRadius: 3, maxBarThickness: 22, yAxisID: "y1" },
      { type: "line", label: t("kpi.eur_per_t"), data: cost.map((c, i) => (tons[i] ? Math.round(c / tons[i]) : null)), borderColor: INK, backgroundColor: INK, tension: .35, pointRadius: 2, borderWidth: 2, yAxisID: "y" },
    ] },
    options: { plugins: { legend: { position: "bottom" } }, scales: dual({ ticks: { callback: (v) => `${v} €/t` } }, { min: 0 }) },
  });
}

export function fillTrendChart(p, target) {
  const labels = p.period.months.map(month);
  return draw("fillTrendChart", {
    type: "line",
    data: { labels, datasets: [
      { label: t("kpi.avg_fill"), data: p.monthly.fill_at_emptying, borderColor: INK, backgroundColor: INK, tension: .35, pointRadius: 2, borderWidth: 2 },
      { label: t("gauge.target"), data: labels.map(() => target), borderColor: STATE.good, borderDash: [5, 4], pointRadius: 0, borderWidth: 1.5 },
    ] },
    options: { plugins: { legend: { position: "bottom" } }, scales: { x: axis({ grid: { display: false } }), y: axis({ min: 0, max: 100, ticks: { callback: (v) => `${v} %` } }) } },
  });
}

const share = (arr) => { const s = arr.reduce((a, b) => a + b, 0) || 1; return arr.map((v) => Math.round((v / s) * 1000) / 10); };

export function weekdayChart(labels, fills, pickups) {
  return draw("weekdayChart", {
    type: "bar",
    data: { labels, datasets: [
      { label: t("tr.fillings"), data: share(fills), backgroundColor: INK, borderRadius: 3, maxBarThickness: 18 },
      { label: t("tr.pickups"), data: share(pickups), backgroundColor: STATE.mute, borderRadius: 3, maxBarThickness: 18 },
    ] },
    options: { plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw, 1)} %` } } },
      scales: { x: axis({ grid: { display: false } }), y: axis({ ticks: { callback: (v) => `${v} %` } }) } },
  });
}

export function hourChart(fills, pickups) {
  const labels = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, "0"));
  return draw("hourChart", {
    type: "line",
    data: { labels, datasets: [
      { label: t("tr.fillings"), data: share(fills), borderColor: INK, backgroundColor: INK, tension: .3, pointRadius: 0, borderWidth: 2, fill: false },
      { label: t("tr.pickups"), data: share(pickups), borderColor: LINK, backgroundColor: LINK, tension: .3, pointRadius: 0, borderWidth: 2, borderDash: [4, 3] },
    ] },
    options: { plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw, 1)} %` } } },
      scales: { x: axis({ grid: { display: false } }), y: axis({ ticks: { callback: (v) => `${v} %` } }) } },
  });
}

export function seasonChart(p) {
  const ms = p.period.months;
  const tp = p.time_patterns;
  const tint = (m) => (["12", "01", "02"].includes(m.slice(5)) ? LINK : ["06", "07", "08"].includes(m.slice(5)) ? STATE.warn : STATE.mute);
  return draw("seasonChart", {
    type: "bar",
    data: { labels: ms.map(month), datasets: [
      { type: "bar", label: t("tr.gainPerDay"), data: tp.gain_per_day_by_month, backgroundColor: ms.map(tint), borderRadius: 3, maxBarThickness: 26, yAxisID: "y" },
      { type: "line", label: t("tr.cyclesPerDay"), data: tp.cycles_per_day_by_month, borderColor: INK, backgroundColor: INK, tension: .35, pointRadius: 2, borderWidth: 2, yAxisID: "y1" },
    ] },
    options: { plugins: { legend: { position: "bottom" } }, scales: dual({ min: 0, ticks: { callback: (v) => `${v} %` } }, { min: 0 }) },
  });
}
