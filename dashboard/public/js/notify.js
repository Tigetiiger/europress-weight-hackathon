// Real notifications, built from the customer's own data: each saving lever becomes
// "problem → SMART AI found a solution → € a year". Used by the bell, the 5-second pop-up and the
// "How you save" list, so the three always say the same thing. Plain words on purpose.
import { t, fmt, eur, addStrings } from "./i18n.js";
import { esc } from "./drawer.js";
import { isFree } from "./plan.js";

const POPUP_DELAY_MS = 5000;
const READ_KEY = "ep.notes.read";
const SHOWN_KEY = "ep.notes.popup";

addStrings({
  en: {
    "np.title": "Notifications", "np.bell": "Notifications ({n} new)", "np.empty": "Nothing new — you are doing well.",
    "np.found": "SMART AI found a solution:", "np.saves": "Saves {eur} a year", "np.show": "Show me how", "np.later": "Later",
    "np.popTitle": "SMART AI found a way to save you {eur} a year",
    "np.early_lifts.p": "{early} of {lifts} pickups came while the machine was not full.", "np.early_lifts.s": "Let SMART order the truck only at 85 % full.",
    "np.sorting.p": "{mixed} % of your waste goes to expensive mixed waste.", "np.sorting.s": "Sort cardboard and plastic out of mixed waste.",
    "np.avoidable_fees.p": "You paid {fees} in extra fees this year.", "np.avoidable_fees.s": "Order on time and keep side waste off the site.",
    "nav.savings": "How to save money",
  },
  fi: {
    "np.title": "Ilmoitukset", "np.bell": "Ilmoitukset ({n} uutta)", "np.empty": "Ei uutta — hyvin menee.",
    "np.found": "SMART AI löysi ratkaisun:", "np.saves": "Säästää {eur} vuodessa", "np.show": "Näytä miten", "np.later": "Myöhemmin",
    "np.popTitle": "SMART AI löysi tavan säästää {eur} vuodessa",
    "np.early_lifts.p": "{early}/{lifts} tyhjennystä tehtiin, kun laite ei ollut täynnä.", "np.early_lifts.s": "Anna SMARTin tilata auto vasta 85 %:n täytössä.",
    "np.sorting.p": "{mixed} % jätteestäsi menee kalliiseen sekajätteeseen.", "np.sorting.s": "Lajittele pahvi ja muovi pois sekajätteestä.",
    "np.avoidable_fees.p": "Maksoit tänä vuonna {fees} lisämaksuja.", "np.avoidable_fees.s": "Tilaa ajoissa ja pidä sivujäte poissa.",
    "nav.savings": "Näin säästät",
  },
});

/** Parameters for each problem sentence, from the customer payload. */
function problemParams(p, lever) {
  const last = (a) => (Array.isArray(a) ? a.filter((v) => v != null).at(-1) : null);
  return {
    early: fmt(p.early_lifts_n), lifts: fmt(p.kpis.lifts.value), eurt: fmt(p.kpis.eur_per_t.value),
    mixed: fmt(last(p.monthly.mixed_share), 0),
    fees: eur(Object.values(lever.breakdown || {}).reduce((a, b) => a + b, 0)), n: fmt(lever.count),
  };
}

/** [{id, problem, solution, eur}] biggest saving first; only levers worth something. */
export function notifications(p) {
  return [...p.levers].filter((l) => l.saving_eur > 0).sort((a, b) => b.saving_eur - a.saving_eur).map((l) => ({
    id: l.id, eur: l.saving_eur,
    problem: t(`np.${l.id}.p`, problemParams(p, l)),
    solution: t(`np.${l.id}.s`),
  }));
}

/** Shared markup: the problem first, then SMART AI's solution, then the money. */
export const problemSolutionHtml = (n) => `<span class="ps-problem">${esc(n.problem)}</span>
  <span class="ps-solution"><b>✦ ${esc(t("np.found"))}</b> ${esc(n.solution)}</span>`;

const storeGet = (k, fallback) => { try { return JSON.parse(localStorage.getItem(k)) ?? fallback; } catch { return fallback; } };
const storeSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } };
const el = (id) => document.getElementById(id);

let current = { orgId: null, list: [], onOpen: () => {} };
let popupTimer = null;

function readSet() { return new Set(storeGet(READ_KEY, {})[current.orgId] || []); }
function markRead(id) {
  const all = storeGet(READ_KEY, {});
  all[current.orgId] = [...new Set([...(all[current.orgId] || []), id])];
  storeSet(READ_KEY, all);
}

function renderBell() {
  const read = readSet();
  const unread = current.list.filter((n) => !read.has(n.id)).length;
  const btn = el("bellBtn");
  btn.querySelector(".bell-n").textContent = unread ? String(unread) : "";
  btn.dataset.unread = String(unread > 0);
  btn.setAttribute("aria-label", t("np.bell", { n: unread }));
  el("bellPanel").innerHTML = `<p class="bell-title">${esc(t("np.title"))}</p>${current.list.length ? `<ul class="bell-list">${current.list.map((n) => `
    <li data-read="${read.has(n.id)}"><button type="button" data-note="${esc(n.id)}">${problemSolutionHtml(n)}
      <span class="ps-eur">${esc(t("np.saves", { eur: eur(n.eur) }))} →</span></button></li>`).join("")}</ul>` : `<p class="muted">${esc(t(isFree() ? "plan.lock.notes" : "np.empty"))}</p>`}`;
}

function open(id) {
  markRead(id);
  togglePanel(false);
  hidePopup();
  renderBell();
  current.onOpen(id);
}

function togglePanel(show) {
  const panel = el("bellPanel");
  const next = show ?? panel.classList.contains("hidden");
  panel.classList.toggle("hidden", !next);
  el("bellBtn").setAttribute("aria-expanded", String(next));
}

function hidePopup() { el("aiPopup")?.classList.add("hidden"); }

function showPopup() {
  const top = current.list[0];
  if (!top || location.hash.startsWith("#ep-") || location.hash === "#operators") return;
  const shown = storeGet(SHOWN_KEY, {});
  if (shown[current.orgId]) return;
  shown[current.orgId] = true;
  storeSet(SHOWN_KEY, shown);
  const pop = el("aiPopup");
  pop.innerHTML = `<p class="pop-title">✦ ${esc(t("np.popTitle", { eur: eur(top.eur) }))}</p>${problemSolutionHtml(top)}
    <div class="pop-actions"><button type="button" class="btn" data-pop="show">${esc(t("np.show"))}</button>
    <button type="button" class="btn ghost" data-pop="later">${esc(t("np.later"))}</button></div>`;
  pop.classList.remove("hidden");
  pop.querySelector('[data-pop="show"]').addEventListener("click", () => open(top.id));
  pop.querySelector('[data-pop="later"]').addEventListener("click", hidePopup);
}

/** Wire the bell once. */
export function initNotifications() {
  el("bellBtn").addEventListener("click", (e) => { e.stopPropagation(); togglePanel(); });
  el("bellPanel").addEventListener("click", (e) => {
    e.stopPropagation();
    const b = e.target.closest("button[data-note]");
    if (b) open(b.dataset.note);
  });
  document.addEventListener("click", () => togglePanel(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { togglePanel(false); hidePopup(); } });
}

/** (Re)build notifications for a customer; schedules the one-time pop-up 5 s after the first load. */
export function setNotifications(p, onOpen) {
  current = { orgId: p.customer.id, list: isFree() ? [] : notifications(p), onOpen }; // Free: nothing automatic
  renderBell();
  clearTimeout(popupTimer);
  hidePopup();
  if (isFree()) return;
  popupTimer = setTimeout(showPopup, POPUP_DELAY_MS);
}
