// "What should I do?" — ready-made answers to the customer's framework of questions.
// Level 1: one-line answer · Level 2 (click): drawer with why + numbered steps + the calculation · Level 3: SMART Advisor.
import { t, fmt, eur, date } from "./i18n.js";
import { esc, openExplain, GO_ICON } from "./drawer.js";

const el = (id) => document.getElementById(id);
const ORDER = { bad: 0, warn: 1, info: 2, good: 3 };
const STATE = { bad: "bad", warn: "warn", info: "info", good: "good" };
let asOf = null; // the date the customer's data stands at (period.as_of), shown next to date-relative claims

function params(a) {
  const q = { ...a.p };
  for (const k of ["eur", "inv_eur"]) if (q[k] != null) q[k] = eur(q[k]);
  for (const k of ["fill", "ontime", "rate", "mixed_a", "mixed_b", "eurt", "km", "full_h", "down_h", "tiny", "inv", "wdiff", "early", "lifts", "wasted", "missed"]) if (typeof q[k] === "number") q[k] = fmt(q[k]);
  if (typeof q.co2 === "number") q.co2 = fmt(q.co2, 1);
  if (q.notice) q.notice = date(q.notice);
  q.asof = asOf ? date(asOf) : "–";
  return q;
}

function traceFor(p, path) {
  if (!path) return null;
  const [head, key] = path.split(".");
  if (head === "levers") return p.levers.find((l) => l.id === key)?.trace;
  if (head === "kpis") return p.kpis[key];
  if (head === "operators") return p.operators[0]?.trace;
  return null;
}

/** The row's one-line answer — the same text on the row and at the top of the drawer. */
const oneLine = (a) => t(`a.${a.id}.${a.status === "good" ? "good" : "head"}`, params(a));

/** The € a row shows (annual saving, or the invoice difference for the mismatch answer); null when there is none. */
const rowEur = (a) => {
  const v = a.p.eur ?? a.p.inv_eur;
  return v > 0 ? v : null;
};

/** Key figure for answers that carry no €; "" when the answer has none. */
const KEY_FIGURE = {
  contract: (q) => `${q.ontime} %`,
  co2: (q) => `${q.co2} t CO₂`,
  safety: (q) => `${q.full_h} h`,
};

function openAnswer(p, a) {
  const q = params(a);
  const euro = rowEur(a);
  openExplain({
    title: t(`a.${a.id}.q`),
    // "" = no big value; the trace's own value must not take its place
    value: euro != null ? eur(euro) : (KEY_FIGURE[a.id]?.(q) ?? ""),
    eyebrow: t("drawer.answer"),
    what: `${oneLine(a)} ${t(`a.${a.id}.why`, q)}`,
    todo: t(`a.${a.id}.steps`, q).split("|").map((s) => s.trim()).filter(Boolean),
    trace: traceFor(p, a.trace),
    focus: `answers.${a.id}`,
  });
}

export function renderAnswers(p) {
  asOf = p.period?.as_of ?? null;
  const list = [...(p.answers || [])].sort((a, b) => ORDER[a.status] - ORDER[b.status]);
  el("answers").innerHTML = list.map((a, i) => {
    const euro = rowEur(a);
    return `<li class="ans" data-status="${a.status}">
      <button type="button" class="ans-row" data-i="${i}">
        <span class="state" data-state="${STATE[a.status]}">${esc(t(`ans.${a.status}`))}</span>
        <span class="ans-q">${esc(t(`a.${a.id}.q`))}</span>
        <b class="ans-h">${esc(oneLine(a))}</b>
        <span class="ans-eur">${euro != null ? esc(eur(euro)) : ""}</span>${GO_ICON}
      </button></li>`;
  }).join("");
  el("answers").querySelectorAll(".ans-row").forEach((b) =>
    b.addEventListener("click", () => openAnswer(p, list[Number(b.dataset.i)])));
}
