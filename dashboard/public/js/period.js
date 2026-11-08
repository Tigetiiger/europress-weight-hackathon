// Calendar period for the customer's numbers: a month range (native month pickers + quick presets).
// periodView() slices every monthly series to the range and recomputes the year-in-numbers KPIs from them,
// so all charts and totals follow the chosen period. Lever savings stay annual (they are "per year").
import { t, month, addStrings } from "./i18n.js";
import { esc } from "./drawer.js";
import { isFree, FREE_PRESETS } from "./plan.js";

const KEY = "ep.period";
const PRESETS = [1, 3, 6, 12];
let range = null; // { from: "YYYY-MM", to: "YYYY-MM" } or null = whole period

addStrings({
  en: { "pd.label": "Period", "pd.from": "From", "pd.to": "To", "pd.m": "{n} mo", "pd.m1": "Last month", "pd.all": "All" },
  fi: { "pd.label": "Ajanjakso", "pd.from": "Alkaen", "pd.to": "Asti", "pd.m": "{n} kk", "pd.m1": "Viime kuu", "pd.all": "Kaikki" },
});

try { range = JSON.parse(sessionStorage.getItem(KEY)); } catch { range = null; }
const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify(range)); } catch { /* storage blocked */ } };

/** Indices [i0, i1] of the chosen range inside the customer's months (whole period when unset/invalid). */
function bounds(months) {
  const i0 = range ? months.indexOf(range.from) : -1;
  const i1 = range ? months.indexOf(range.to) : -1;
  const [a, b] = i0 >= 0 && i1 >= i0 ? [i0, i1] : [0, months.length - 1];
  const maxFree = Math.max(...FREE_PRESETS);
  return isFree() && b - a + 1 > maxFree ? [months.length - maxFree, months.length - 1] : [a, b];
}

export function periodLabel(p) {
  const [i0, i1] = bounds(p.period.months);
  const m = p.period.months;
  return i0 === i1 ? month(m[i0]) : `${month(m[i0])} – ${month(m[i1])} ${m[i1].slice(0, 4)}`;
}

const sum = (a) => a.reduce((x, y) => x + (y || 0), 0);
const sliceObj = (o, i0, i1) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, v.slice(i0, i1 + 1)]));

/** A copy of the customer payload restricted to the chosen months (shallow; untouched fields are shared). */
export function periodView(p) {
  const [i0, i1] = bounds(p.period.months);
  if (i0 === 0 && i1 === p.period.months.length - 1) return p;
  const mo = p.monthly;
  const monthly = {
    ...mo,
    cost_by_stream: sliceObj(mo.cost_by_stream, i0, i1), tonnes_by_stream: sliceObj(mo.tonnes_by_stream, i0, i1),
    density_by_stream: sliceObj(mo.density_by_stream, i0, i1),
    recycling_rate: mo.recycling_rate.slice(i0, i1 + 1), mixed_share: mo.mixed_share.slice(i0, i1 + 1),
    pickups: mo.pickups.slice(i0, i1 + 1), fill_at_emptying: mo.fill_at_emptying.slice(i0, i1 + 1),
  };
  const cost = sum(Object.values(monthly.cost_by_stream).map(sum));
  const tonnes = sum(Object.values(monthly.tonnes_by_stream).map(sum));
  const mixedT = sum(monthly.tonnes_by_stream.mixed_waste || []);
  const lifts = sum(monthly.pickups);
  const fillW = sum(monthly.fill_at_emptying.map((f, i) => (f || 0) * (monthly.pickups[i] || 0)));
  const k = p.kpis;
  const kv = (key, value) => ({ ...k[key], value });
  const share = p.kpis.lifts.value ? lifts / p.kpis.lifts.value : 0;
  return {
    ...p,
    period: { ...p.period, months: p.period.months.slice(i0, i1 + 1), from: p.period.months[i0], to: p.period.months[i1] },
    monthly,
    kpis: {
      ...k,
      total_cost: kv("total_cost", cost), tonnes: kv("tonnes", tonnes), eur_per_t: kv("eur_per_t", tonnes ? cost / tonnes : null),
      recycling_rate: kv("recycling_rate", tonnes ? (100 * (tonnes - mixedT)) / tonnes : null), lifts: kv("lifts", lifts),
      avg_fill: kv("avg_fill", lifts ? fillW / lifts : null), co2: kv("co2", k.co2.value * share),
    },
  };
}

/** Calendar control: two month inputs + presets. onChange() re-renders the page. */
export function renderPeriodPicker(container, p, onChange) {
  const m = p.period.months;
  const [i0, i1] = bounds(m);
  const n = i1 - i0 + 1;
  const locked = (k) => isFree() && !FREE_PRESETS.includes(k);
  const presets = PRESETS.map((k) => `<button type="button" data-n="${k}" aria-pressed="${n === k && i1 === m.length - 1}"${locked(k) ? ` disabled title="${esc(t("plan.lock.period"))}"` : ""}>${locked(k) ? "🔒 " : ""}${esc(k === 1 ? t("pd.m1") : k === m.length ? t("pd.all") : t("pd.m", { n: k }))}</button>`).join("");
  container.innerHTML = `<div class="pd">
    <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>
    <span class="sr">${esc(t("pd.label"))}</span>
    <label><span class="sr">${esc(t("pd.from"))}</span><input type="month" data-pd="from"${isFree() ? " disabled" : ""} min="${m[0]}" max="${m[m.length - 1]}" value="${m[i0]}"></label>
    <span aria-hidden="true">–</span>
    <label><span class="sr">${esc(t("pd.to"))}</span><input type="month" data-pd="to"${isFree() ? " disabled" : ""} min="${m[0]}" max="${m[m.length - 1]}" value="${m[i1]}"></label>
    <div class="seg pd-presets" role="group" aria-label="${esc(t("pd.label"))}">${presets}</div></div>`;
  const set = (from, to) => { range = from === m[0] && to === m[m.length - 1] ? null : { from, to }; save(); onChange(); };
  container.querySelectorAll("input[data-pd]").forEach((inp) => inp.addEventListener("change", () => {
    const from = container.querySelector('[data-pd="from"]').value, to = container.querySelector('[data-pd="to"]').value;
    if (m.includes(from) && m.includes(to) && from <= to) set(from, to);
  }));
  container.querySelector(".pd-presets").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-n]");
    if (b) set(m[Math.max(0, m.length - Number(b.dataset.n))], m[m.length - 1]);
  });
}
