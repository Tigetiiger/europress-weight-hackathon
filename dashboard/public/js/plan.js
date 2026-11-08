// Free / SMART plan for the whole dashboard. Free is the bare minimum and fully manual (no fill levels,
// no forecasts, no automatic ordering or alerts); SMART unlocks the full stack
// (live weight, every saving, long periods, forecasts, unlimited SMART Advisor). Demo toggle in the header;
// a real deployment would read the plan from the customer's subscription instead of localStorage.
import { t, addStrings } from "./i18n.js";
import { esc } from "./drawer.js";

const KEY = "ep.plan";
const ASK_KEY = "ep.free.asks";
export const FREE_ASKS_PER_DAY = 3;
export const FREE_PRESETS = [1, 3];
let plan = "smart";
try { plan = localStorage.getItem(KEY) === "free" ? "free" : "smart"; } catch { /* storage blocked */ }

addStrings({
  en: {
    "plan.label": "Plan", "plan.free": "Free", "plan.smart": "SMART", "plan.unlock": "Switch to SMART",
    "plan.lock.weight": "With SMART you see the weight in every container and what each pickup will cost — before the truck comes. Free shows only your machines, your pickups and your costs.",
    "plan.lock.fill": "Fill level in SMART", "plan.lock.fillLong": "Fill levels, when each machine will be full and automatic truck ordering are part of SMART.",
    "plan.lock.notes": "Automatic alerts and savings tips are part of SMART.", "plan.lock.trends": "Trends and findings are part of SMART.",
    "plan.manual": "You order pickups yourself from {op}", "plan.manualShort": "Manual order", "plan.order": "Order a pickup", "plan.ordered": "Pickup requested from {op} — they confirm the date", "plan.lock.tonnes": "Tonnes and price per tonne are part of SMART.",
    "plan.lock.savings": "{n} more savings worth {eur} a year are waiting in SMART.",
    "plan.lock.when": "Daily and hourly patterns are part of SMART.",
    "plan.lock.forecast": "The 14-day pickup forecast and SMART's value for your routes are part of SMART.",
    "plan.lock.period": "Longer periods are part of SMART.", "plan.lock.drivers": "Which days to call in drivers, and how to flatten the peaks, is part of SMART.",
    "plan.asks": "Free: {n} of {max} questions left today", "plan.asksOut": "You used today's {max} free questions. SMART has no limit.",
  },
  fi: {
    "plan.label": "Paketti", "plan.free": "Free", "plan.smart": "SMART", "plan.unlock": "Vaihda SMARTiin",
    "plan.lock.weight": "SMARTilla näet jokaisen säiliön painon ja mitä kukin tyhjennys maksaa — ennen kuin auto tulee. Free näyttää vain laitteesi, tyhjennyksesi ja kulusi.",
    "plan.lock.fill": "Täyttöaste SMARTissa", "plan.lock.fillLong": "Täyttöasteet, milloin laite täyttyy ja automaattinen autotilaus kuuluvat SMARTiin.",
    "plan.lock.notes": "Automaattiset hälytykset ja säästövinkit kuuluvat SMARTiin.", "plan.lock.trends": "Trendit ja havainnot kuuluvat SMARTiin.",
    "plan.manual": "Tilaat tyhjennykset itse: {op}", "plan.manualShort": "Tilaus itse", "plan.order": "Tilaa tyhjennys", "plan.ordered": "Tyhjennyspyyntö lähetetty: {op} — he vahvistavat päivän", "plan.lock.tonnes": "Tonnit ja tonnihinta kuuluvat SMARTiin.",
    "plan.lock.savings": "{n} muuta säästöä, yhteensä {eur} vuodessa, odottaa SMARTissa.",
    "plan.lock.when": "Päivä- ja tuntikohtaiset tiedot kuuluvat SMARTiin.",
    "plan.lock.forecast": "14 päivän tyhjennysennuste ja SMARTin arvo reiteillesi kuuluvat SMARTiin.",
    "plan.lock.period": "Pidemmät jaksot kuuluvat SMARTiin.", "plan.lock.drivers": "Milloin kutsua lisää kuljettajia ja miten huiput tasoitetaan, kuuluu SMARTiin.",
    "plan.asks": "Free: {n}/{max} kysymystä jäljellä tänään", "plan.asksOut": "Käytit tämän päivän {max} ilmaista kysymystä. SMARTissa ei ole rajaa.",
  },
});

export const getPlan = () => plan;
export const isFree = () => plan === "free";

let onChange = () => {};
export function setPlan(next) {
  plan = next === "free" ? "free" : "smart";
  try { localStorage.setItem(KEY, plan); } catch { /* storage blocked */ }
  document.body.dataset.plan = plan;
  onChange();
}

/** Header toggle; `rerender` is called after a change. */
export function initPlanToggle(container, rerender) {
  onChange = rerender;
  document.body.dataset.plan = plan;
  container.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-plan]");
    if (b) setPlan(b.dataset.plan);
  });
  document.addEventListener("click", (e) => { if (e.target.closest("[data-unlock]")) setPlan("smart"); });
}

export function renderPlanToggle(container) {
  container.innerHTML = ["free", "smart"].map((p) => `<button type="button" data-plan="${p}" aria-pressed="${p === plan}">${esc(t(`plan.${p}`))}</button>`).join("");
  container.setAttribute("aria-label", t("plan.label"));
}

/** Free: blur the element's content and lay a short "part of SMART" message with an unlock button over it. */
export function gate(el, messageKey, params = {}) {
  if (!el) return;
  el.querySelector(":scope > .plan-lock")?.remove();
  el.classList.toggle("is-locked", isFree());
  if (!isFree()) return;
  el.insertAdjacentHTML("beforeend", `<div class="plan-lock" role="note"><p>🔒 ${esc(t(messageKey, params))}</p>
    <button type="button" class="btn" data-unlock>${esc(t("plan.unlock"))}</button></div>`);
}

/** One line that says what is locked, with the unlock button (for lists). */
export const lockRow = (messageKey, params = {}) => `<div class="plan-lockrow"><span>🔒 ${esc(t(messageKey, params))}</span>
  <button type="button" class="btn ghost" data-unlock>${esc(t("plan.unlock"))}</button></div>`;

// ---------------------------------------------------------------- SMART Advisor allowance (Free)
const today = () => new Date().toISOString().slice(0, 10);
function asksUsed() {
  try { const v = JSON.parse(localStorage.getItem(ASK_KEY)); return v?.day === today() ? v.n : 0; } catch { return 0; }
}
/** True when the question may be sent; counts it in Free. */
export function takeAsk() {
  if (!isFree()) return true;
  const n = asksUsed();
  if (n >= FREE_ASKS_PER_DAY) return false;
  try { localStorage.setItem(ASK_KEY, JSON.stringify({ day: today(), n: n + 1 })); } catch { /* storage blocked */ }
  return true;
}
export const asksLeftText = () => (isFree() ? t("plan.asks", { n: Math.max(0, FREE_ASKS_PER_DAY - asksUsed()), max: FREE_ASKS_PER_DAY }) : "");
export const asksOutText = () => t("plan.asksOut", { max: FREE_ASKS_PER_DAY });
