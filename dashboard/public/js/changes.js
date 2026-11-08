// "What changed": last N months vs the N months before (N = 3 / 6), or the year's first vs last quarter (12),
// computed from the monthly series. Each row: measure · before → after · change (good/bad) · what to do if worse.
import { t, fmt, eur, month, addStrings } from "./i18n.js";
import { esc } from "./drawer.js";
import { isFree } from "./plan.js";

const SPANS = [3, 6, 12];
const QUARTER = 3;
let span = 3;

addStrings({
  en: {
    "chg.title": "What changed", "chg.m": "{n} months", "chg.vs": "{a} vs {b}", "chg.none": "No big changes in this period.",
    "chg.cost": "Waste cost", "chg.tonnes": "Waste collected", "chg.eurt": "Cost per tonne", "chg.recycling": "Recycling rate",
    "chg.mixed": "Mixed-waste share", "chg.pickups": "Pickups", "chg.fill": "Fill at emptying",
    "chg.do.cost": "Check the potential savings above — the biggest one first.", "chg.do.eurt": "Fuller pickups lower the price per tonne.",
    "chg.do.recycling": "Sort cardboard and plastic out of mixed waste.", "chg.do.mixed": "Put a cardboard bin next to the mixed-waste machine.",
    "chg.do.pickups": "Let SMART order trucks only when machines are full.", "chg.do.fill": "Raise the order level to 85 % full.",
    "chg.do.tonnes": "More waste: check that machines are big enough.",
  },
  fi: {
    "chg.title": "Mitä muuttui", "chg.m": "{n} kk", "chg.vs": "{a} vs. {b}", "chg.none": "Ei suuria muutoksia tällä jaksolla.",
    "chg.cost": "Jätekulut", "chg.tonnes": "Kerätty jäte", "chg.eurt": "Tonnihinta", "chg.recycling": "Kierrätysaste",
    "chg.mixed": "Sekajätteen osuus", "chg.pickups": "Tyhjennykset", "chg.fill": "Täyttö tyhjennettäessä",
    "chg.do.cost": "Katso säästöpotentiaali yllä — suurin ensin.", "chg.do.eurt": "Täydemmät tyhjennykset laskevat tonnihintaa.",
    "chg.do.recycling": "Lajittele pahvi ja muovi pois sekajätteestä.", "chg.do.mixed": "Laita pahviastia sekajätelaitteen viereen.",
    "chg.do.pickups": "Anna SMARTin tilata auto vasta, kun laite on täynnä.", "chg.do.fill": "Nosta tilausraja 85 %:iin.",
    "chg.do.tonnes": "Enemmän jätettä: tarkista, että laitteet ovat riittävän suuria.",
  },
});

const sum = (a) => a.reduce((x, y) => x + (y || 0), 0);

/** Totals for a block of month indices. */
function block(p, idx) {
  const mo = p.monthly;
  const pick = (a) => idx.map((i) => a[i] || 0);
  const cost = sum(Object.values(mo.cost_by_stream).map((v) => sum(pick(v))));
  const tonnes = sum(Object.values(mo.tonnes_by_stream).map((v) => sum(pick(v))));
  const mixed = sum(pick(mo.tonnes_by_stream.mixed_waste || []));
  const pickups = sum(pick(mo.pickups));
  const fill = pickups ? sum(idx.map((i) => (mo.fill_at_emptying[i] || 0) * (mo.pickups[i] || 0))) / pickups : null;
  return { cost, tonnes, eurt: tonnes ? cost / tonnes : null, recycling: tonnes ? (100 * (tonnes - mixed)) / tonnes : null,
           mixed: tonnes ? (100 * mixed) / tonnes : null, pickups, fill };
}

/** Free is the bare minimum: only cost and pickups. Weight-based measures and fill are SMART. */
const WEIGHT_MEASURES = new Set(["tonnes", "eurt", "recycling", "mixed", "fill"]);
// measure → [good direction, unit, decimals]
const MEASURES = { cost: ["down", "€", 0], tonnes: ["info", "t", 1], eurt: ["down", "€/t", 0], recycling: ["up", "%", 1],
                   mixed: ["down", "%", 1], pickups: ["down", "", 0], fill: ["up", "%", 0] };
const show = (v, unit, d) => (v == null ? "–" : unit === "€" ? eur(v) : `${fmt(v, d)}${unit ? ` ${unit}` : ""}`);

function rows(p) {
  const n = p.period.months.length;
  const k = span === 12 ? QUARTER : span;
  const after = Array.from({ length: k }, (_, i) => n - k + i);
  const before = span === 12 ? Array.from({ length: k }, (_, i) => i) : after.map((i) => i - k).filter((i) => i >= 0);
  const a = block(p, before), b = block(p, after);
  const label = (idx) => `${month(p.period.months[idx[0]])}–${month(p.period.months[idx[idx.length - 1]])}`;
  const list = Object.entries(MEASURES).filter(([key]) => !(isFree() && WEIGHT_MEASURES.has(key))).map(([key, [good, unit, d]]) => {
    const pct = a[key] ? ((b[key] - a[key]) / Math.abs(a[key])) * 100 : null;
    const tone = good === "info" || pct == null || Math.abs(pct) < 1 ? "mute" : (good === "up") === pct > 0 ? "good" : "bad";
    return { key, from: show(a[key], unit, d), to: show(b[key], unit, d), pct, tone };
  }).sort((x, y) => (x.tone === "bad" ? 0 : 1) - (y.tone === "bad" ? 0 : 1) || Math.abs(y.pct ?? 0) - Math.abs(x.pct ?? 0));
  return { list, period: t("chg.vs", { a: label(after), b: label(before) }) };
}

/** Render into the Overview "What changed" card (title row gets the 3/6/12 switch). */
export function renderChanges(p, { list: listEl, period: periodEl, toggle: toggleEl }) {
  const draw = () => {
    const { list, period } = rows(p);
    periodEl.textContent = period;
    toggleEl.innerHTML = SPANS.map((n) => `<button type="button" data-span="${n}" aria-pressed="${n === span}">${esc(t("chg.m", { n }))}</button>`).join("");
    listEl.innerHTML = list.length ? list.map((r) => `<li class="chg-row" data-tone="${r.tone}">
        <span class="chg-name">${esc(t(`chg.${r.key}`))}${r.tone === "bad" ? `<span class="chg-do">${esc(t(`chg.do.${r.key}`))}</span>` : ""}</span>
        <span class="chg-vals"><span class="chg-from">${esc(r.from)}</span> → <b>${esc(r.to)}</b></span>
        <span class="state" data-state="${r.tone}">${r.pct == null ? "–" : `${r.pct > 0 ? "+" : ""}${fmt(r.pct, 1)} %`}</span></li>`).join("")
      : `<li class="muted">${esc(t("chg.none"))}</li>`;
  };
  toggleEl.onclick = (e) => {
    const b = e.target.closest("button[data-span]");
    if (b) { span = Number(b.dataset.span); draw(); }
  };
  draw();
}
