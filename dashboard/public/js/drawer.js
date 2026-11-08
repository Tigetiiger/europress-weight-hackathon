// Explanation drawer: one layout for every drawer in the app. Value → what it means → what to do →
// the calculation (collapsed) → an always-visible "Ask SMART Advisor" footer.
// Also the "value chip" (value + magnifier) that opens it.
import { t, getLang, fmt } from "./i18n.js";

const el = (id) => document.getElementById(id);
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** Translation that is empty instead of the key name while a key is missing. */
export const tx = (key, params) => { const s = t(key, params); return s === key ? "" : s; };

const magnifier = (cls) => `<svg class="${cls}" viewBox="3.5 3.5 18 18" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4-4"/></svg>`;
/** Trailing magnifier for rows that open the drawer (answers, table rows). */
export const GO_ICON = magnifier("go-ic");
export const ROW_GO = `<td class="row-go">${GO_ICON}</td>`;

/**
 * Explainable value: `html` is already escaped; `name` is plain text for the screen-reader label;
 * `key` comes back to the bindChips callback.
 */
export function chip(html, name, key) {
  return `<button type="button" class="xv" data-xv="${esc(key)}" aria-label="${esc(t("xv.aria", { name }))}"><span class="xv-v">${html}</span>${magnifier("xv-ic")}</button>`;
}
export function bindChips(root, open) {
  root.querySelectorAll("button[data-xv]").forEach((b) => b.addEventListener("click", () => open(b.dataset.xv)));
}

let lastFocus = null;
let current = { question: "", focus: "" };
/** fn(question, focus): called by the drawer footer button. */
export function setAskHandler(fn) {
  el("drawerAsk").onclick = () => fn(current.question, current.focus);
}

const FOCUSABLE = "button, [href], summary, [tabindex]:not([tabindex='-1'])";
function trapTab(e) {
  if (e.key !== "Tab") return;
  const items = [...e.currentTarget.querySelectorAll(FOCUSABLE)].filter((n) => n.getClientRects().length);
  const first = items[0], last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

export function openPanel(panel) {
  lastFocus = document.activeElement;
  el("scrim").classList.remove("hidden");
  panel.classList.remove("hidden");
  panel.onkeydown = trapTab;
  panel.querySelector("[data-close]")?.focus();
}
export function closePanels() {
  el("scrim").classList.add("hidden");
  document.querySelectorAll(".drawer").forEach((d) => d.classList.add("hidden"));
  lastFocus?.focus?.();
}

function formatValue(v, unit) {
  if (unit === "EUR" || unit === "EUR/yr") return `${fmt(v)} €${unit === "EUR/yr" ? (getLang() === "fi" ? " / v" : " / yr") : ""}`;
  if (unit === "EUR/t") return `${fmt(v)} €/t`;
  if (unit === "%") return `${fmt(v, 1)} %`;
  return `${fmt(v, Number.isInteger(v) ? 0 : 1)} ${unit}`;
}

function valueText(value, unit, trace) {
  if (typeof value === "string") return value;
  if (value != null) return formatValue(value, unit);
  return trace?.value != null ? formatValue(trace.value, trace.unit) : "";
}

const section = (head, body) => `<section class="drawer-section"><h3>${esc(head)}</h3>${body}</section>`;
const SENTENCE_END = /(?<=[.!?])\s+(?=[A-ZÅÄÖ0-9])/;
const paragraphs = (text) => String(text).split(SENTENCE_END).map((s) => `<p>${esc(s)}</p>`).join("");

function whatHtml(what, facts) {
  if (!what && !facts.length) return "";
  const dl = facts.length ? `<dl class="drawer-facts">${facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>` : "";
  return section(t("drawer.what"), `${what ? `<div class="explain">${paragraphs(what)}</div>` : ""}${dl}`);
}

function todoHtml(todo) {
  if (Array.isArray(todo)) return todo.length ? section(t("drawer.todo"), `<ol class="todo todo-steps">${todo.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>`) : "";
  return todo ? section(t("drawer.todo"), `<div class="todo">${esc(todo)}</div>`) : "";
}

function rowsTable(rows) {
  if (!rows?.rows?.length) return "";
  const head = rows.columns.map((c) => `<th>${esc(c)}</th>`).join("");
  const body = rows.rows.map((r) => `<tr>${r.map((v) => `<td class="${typeof v === "number" ? "num" : ""}">${esc(typeof v === "number" ? fmt(v, Number.isInteger(v) ? 0 : 2) : v)}</td>`).join("")}</tr>`).join("");
  return `<section class="drawer-section">
    <h3>${t("drawer.rows")} <span class="muted">(${t("drawer.of", { n: rows.rows.length, total: fmt(rows.total_rows) })})</span></h3>
    <div class="table-wrap"><table class="table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
  </section>`;
}

/** "How it is calculated", collapsed — open by default only when there is nothing else to read. */
function calcHtml(trace, open) {
  if (!trace) return "";
  const formula = trace.formula?.[getLang()] || trace.formula?.en || "";
  const sources = trace.sources?.length ? section(t("drawer.sources"), `<div class="sources">${trace.sources.map((s) => `<code>${esc(s)}</code>`).join("")}</div>`) : "";
  return `<details class="calc"${open ? " open" : ""}><summary>${esc(t("ans.calc"))}</summary><div class="calc-body">
    ${formula ? section(t("drawer.formula"), `<div class="formula">${esc(formula)}</div>`) : ""}
    ${trace.assumption ? `<div class="assumption"><span class="state" data-state="warn">${t("drawer.assumption")}</span><span>${esc(trace.assumption)}</span></div>` : ""}
    ${sources}${rowsTable(trace.rows)}</div></details>`;
}

/** Prefilled advisor question; drops the "()" when the drawer has no value (info texts, some answers). */
function askQuestion(title, value) {
  const q = t("drawer.askQ", { title, value });
  return value ? q : q.replace(/\s*\(\s*\)/, "");
}

/**
 * The one drawer renderer.
 * @param {{title:string, value?:number|string, unit?:string, what?:string, todo?:string|string[],
 *          trace?:object|null, focus?:string, facts?:Array<[string,string]>, eyebrow?:string}} o
 *   value: a number (formatted with unit) or a ready string; defaults to the trace's value ("" = no big value).
 *   eyebrow: small label above the title; shown even without a value. Default: "Where does this number come from?", only with a value.
 *   todo:  a sentence, or a list of steps (numbered).  facts: short label/value pairs under "What it means".
 */
export function openExplain({ title, value, unit = "", what = "", todo = "", trace = null, focus = "", facts = [], eyebrow = "" }) {
  const shown = valueText(value, unit, trace);
  el("drawerTitle").textContent = title;
  el("drawerEyebrow").textContent = eyebrow || t("drawer.title");
  el("drawerEyebrow").hidden = !eyebrow && !shown;
  el("drawerBody").innerHTML = [shown ? `<div class="drawer-value">${esc(shown)}</div>` : "",
    whatHtml(what, facts), todoHtml(todo), calcHtml(trace, !what && !facts.length)].join("");
  el("drawerBody").scrollTop = 0;
  current = { question: askQuestion(title, shown), focus };
  openPanel(el("drawer"));
}

const EXPLAIN_KEY = { kpis: "x.kpi", levers: "x.lever", problems: "x.prob" };
/** Plain explanation for a metric key ("kpis.avg_fill" → x.kpi.avg_fill), "" when there is none. */
function explainFor(focus) {
  const [head, id] = String(focus).split(".");
  return EXPLAIN_KEY[head] && id ? tx(`${EXPLAIN_KEY[head]}.${id}`) : "";
}

/**
 * @param {{value:number, unit:string, formula:{en:string, fi:string}, sources:string[], rows?:object, assumption?:string}} trace
 * @param {string} title   human label of the metric
 * @param {string} focus   metric key passed to the AI ("kpis.total_cost")
 * @param {string} todo    optional "what to do" line
 */
/** Drawer with caller-built (already escaped) HTML, e.g. a structured sales lead. */
export function openCustom({ eyebrow = "", title, html, question = "", focus = "" }) {
  el("drawerTitle").textContent = title;
  el("drawerEyebrow").textContent = eyebrow;
  el("drawerEyebrow").hidden = !eyebrow;
  el("drawerBody").innerHTML = html;
  el("drawerBody").scrollTop = 0;
  current = { question: question || title, focus };
  openPanel(el("drawer"));
  return el("drawerBody");
}

export function openTrace(trace, title, focus = "", todo = "") {
  if (!trace) return;
  openExplain({ title, trace, focus, todo, what: explainFor(focus) });
}

/** Plain explanation for the ⓘ buttons. */
export function openInfo(title, text, focus = "") {
  openExplain({ title, what: text, focus });
}

/** Device trace built client-side from the device row. */
export function deviceTrace(d) {
  return {
    value: d.eur_per_t, unit: "EUR/t",
    formula: {
      en: `${fmt(d.cost_eur)} € invoiced ÷ ${fmt(d.tonnes, 2)} t collected over ${d.lifts} lifts. Average fill at emptying ${fmt(d.avg_fill_pct)} %, ${d.wasted_trips} wasted trips.`,
      fi: `${fmt(d.cost_eur)} € laskutettu ÷ ${fmt(d.tonnes, 2)} t kerätty ${d.lifts} tyhjennyksessä. Keskimääräinen täyttöaste ${fmt(d.avg_fill_pct)} %, ${d.wasted_trips} turhaa käyntiä.`,
    },
    sources: ["finance/invoice_lines.parquet", "operations/collection_events.parquet", "reference/devices.parquet"],
    rows: {
      columns: ["device_id", "site", "stream", "volume_m3", "policy", "operator_id", "lifts", "tonnes", "cost_eur", "eur_per_t", "avg_fill_pct"],
      rows: [[d.device_id, d.site, d.stream, d.volume_m3, d.policy, d.operator_id, d.lifts, d.tonnes, d.cost_eur, d.eur_per_t, d.avg_fill_pct]],
      total_rows: 1,
    },
  };
}
