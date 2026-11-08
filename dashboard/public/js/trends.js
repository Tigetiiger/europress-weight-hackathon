// Trends tab: findings (last 3 vs first 3 months), monthly trend charts, and when the work happens.
import { t, fmt, getLang } from "./i18n.js";
import { esc, openTrace, openExplain } from "./drawer.js";
import { isFree } from "./plan.js";
import { mixedTrendChart, streamTrendChart, costPickupChart, fillTrendChart, weekdayChart, hourChart, seasonChart } from "./charts.js";

const el = (id) => document.getElementById(id);
const loc = () => (getLang() === "fi" ? "fi-FI" : "en-GB");
const dayName = (i, style = "long") => new Intl.DateTimeFormat(loc(), { weekday: style }).format(new Date(2024, 0, 1 + i)); // Mon = 0
const cap = (x) => x.charAt(0).toUpperCase() + x.slice(1);
const hh = (h) => `${String(h).padStart(2, "0")}:00`;

// direction that is good for the customer, per finding
const GOOD_WHEN = { mixed: "down", eurt: "down", fill: "up", pickups: "down", recycling: "up" };

function tone(f) {
  if (!(f.id in GOOD_WHEN) || f.a == null || f.b == null || f.a === f.b) return "info";
  const up = f.b > f.a;
  return (GOOD_WHEN[f.id] === "up") === up ? "good" : "warn";
}

function findingText(f) {
  const n1 = (v) => fmt(v, Number.isInteger(v) ? 0 : 1);
  switch (f.id) {
    case "mixed": return t("trf.mixed", { a: n1(f.a), b: n1(f.b), sa: fmt(f.share_a), sb: fmt(f.share_b) });
    case "eurt": return t("trf.eurt", { a: fmt(f.a), b: fmt(f.b) });
    case "fill": return t("trf.fill", { a: fmt(f.a), b: fmt(f.b) });
    case "pickups": return t("trf.pickups", { a: n1(f.a), b: n1(f.b) });
    case "recycling": return t("trf.recycling", { a: fmt(f.a), b: fmt(f.b) });
    case "weekday": return t("trf.weekday", { day: cap(dayName(f.day)), share: fmt(f.share), quiet: dayName(f.quiet) });
    case "hours": return t("trf.hours", { from: hh(f.from), to: hh(f.to), share: fmt(f.share) });
    case "season": return t(f.diff >= 0 ? "trf.seasonWinter" : "trf.seasonSummer", { diff: fmt(Math.abs(f.diff)), w: fmt(f.w, 1), s: fmt(f.s, 1) });
    case "pickupday": return t("trf.pickupday", { day: dayName(f.day) });
    default: return "";
  }
}

/** The short value on a tile: "a → b" for the changes, the key fact for the patterns. */
function shortValue(f) {
  const n1 = (v) => fmt(v, Number.isInteger(v) ? 0 : 1);
  switch (f.id) {
    case "eurt": return `${fmt(f.a)} → ${fmt(f.b)} €/t`;
    case "fill": case "recycling": return `${fmt(f.a)} → ${fmt(f.b)} %`;
    case "mixed": case "pickups": return `${n1(f.a)} → ${n1(f.b)}`;
    case "weekday": return cap(dayName(f.day, "short"));
    case "hours": return `${hh(f.from)}–${hh(f.to)}`;
    case "season": return `${f.diff >= 0 ? "+" : "−"}${fmt(Math.abs(f.diff))} %`;
    case "pickupday": return cap(dayName(f.day, "short"));
    default: return "";
  }
}

/** Compact tiles: title + one value; the full sentence opens in the drawer. */
function renderFindings(p) {
  // Free shows no weight: findings in tonnes or €/t are SMART.
  const list = (p.trend_findings || []).filter((f) => !(isFree() && (f.id === "eurt" || f.id === "mixed")));
  el("trendFindings").innerHTML = list.map((f, i) => `<li class="tf" data-tone="${tone(f)}"><button type="button" data-f="${i}">
      <b>${esc(t(`trt.${f.id}`))}</b><span class="tf-v">${esc(shortValue(f))}</span></button></li>`).join("");
  el("trendFindings").querySelectorAll("button[data-f]").forEach((b) => b.addEventListener("click", () => {
    const f = list[Number(b.dataset.f)];
    openExplain({ title: t(`trt.${f.id}`), value: shortValue(f), what: findingText(f), focus: `trend_findings.${f.id}` });
  }));
}

function renderHeatmap(tp) {
  const max = Math.max(1, ...tp.heat.flat());
  const head = `<span></span>${Array.from({ length: 24 }, (_, h) => `<span class="hx">${h % 3 === 0 ? String(h).padStart(2, "0") : ""}</span>`).join("")}`;
  const rows = tp.heat.map((row, d) => `<span class="hy">${esc(dayName(d, "short"))}</span>${row.map((v, h) =>
    `<i style="opacity:${(0.06 + 0.94 * (v / max)).toFixed(2)}" title="${esc(dayName(d))} ${hh(h)}: ${fmt(v)} ${esc(t("tr.cycles"))}"></i>`).join("")}`).join("");
  el("heatmap").innerHTML = head + rows;
  el("heatmap").setAttribute("aria-label", t("tr.whenTitle"));
}

export function renderTrends(p, target = 85) {
  renderFindings(p);
  mixedTrendChart(p); streamTrendChart(p); costPickupChart(p); fillTrendChart(p, target);
  const tp = p.time_patterns;
  renderHeatmap(tp);
  weekdayChart(Array.from({ length: 7 }, (_, i) => dayName(i, "short")), tp.fill_by_weekday, tp.pickups_by_weekday);
  hourChart(tp.fill_by_hour, tp.pickups_by_hour);
  seasonChart(p);
  const s = tp.season;
  el("seasonNote").textContent = t("tr.seasonNote", { w: fmt(s.winter_gain, 1), s: fmt(s.summer_gain, 1), wc: fmt(s.winter_cycles, 1), sc: fmt(s.summer_cycles, 1) });
  el("heatmap").onclick = () => openTrace(tp.trace, t("tr.whenTitle"), "time_patterns");
}
