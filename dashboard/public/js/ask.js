// SMART Advisor: floating, context-aware chat docked bottom-right. Streams Server-Sent Events from POST /api/ask
// (headless Claude Code on the server, read-only over the data). It always knows the page, the account and the
// last thing the user clicked, so questions can be short.
import { t, getLang } from "./i18n.js";
import { takeAsk, asksLeftText, asksOutText } from "./plan.js";
import { esc } from "./drawer.js";

const el = (id) => document.getElementById(id);
let context = { view: "customer", id: "", page: "overview", name: "" };
let busy = false;

// Ready-made questions per page (Tommi: "figure out what ready-made answers it can already give you")
const SUGGEST = {
  overview: ["q.1", "q.fleet", "q.mismatch"],
  savings: ["q.1", "q.4", "q.sales"],
  machines: ["q.4", "q.trucks", "q.mismatch"],
  contracts: ["q.3", "q.sla"],
  weight: ["q.weight", "q.5"],
  trends: ["q.2", "q.when", "q.5"],
  account: ["q.contract", "q.3"],
  faq: ["q.weight", "q.1"],
  operators: ["qo.1", "qo.2"],
  fleet: ["qe.1", "qe.2", "qe.3"],
};

export function setAskContext(ctx) {
  context = { ...context, ...ctx };
  renderSuggestions();
  renderContextLine();
}

function renderContextLine() {
  const pageName = t(`page.${context.page}`) || context.page;
  el("aiContext").textContent = t("ai.context", { page: pageName, who: context.name || context.id });
}

function renderSuggestions() {
  const keys = context.view === "operator" ? SUGGEST.operators : context.view === "fleet" ? SUGGEST.fleet : (SUGGEST[context.page] || SUGGEST.overview);
  el("aiSuggest").innerHTML = keys.map((k) => `<button type="button" data-q="${k}">${esc(t(k))}</button>`).join("");
}

/** Tiny, safe markdown: escape first, then bold / code / bullets / paragraphs. */
function md(text) {
  const lines = esc(text).split(/\n/);
  let html = "", list = false;
  for (const raw of lines) {
    const line = raw.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
    if (/^\s*[-*•]\s+/.test(raw) || /^\s*\d+\.\s+/.test(raw)) {
      if (!list) { html += "<ul>"; list = true; }
      html += `<li>${line.replace(/^\s*([-*•]|\d+\.)\s+/, "")}</li>`;
      continue;
    }
    if (list) { html += "</ul>"; list = false; }
    if (/^(source|lähde)\s*:/i.test(raw.trim())) html += `<span class="src">${line}</span>`;
    else if (raw.trim()) html += `<p>${line.replace(/^#+\s*/, "")}</p>`;
  }
  return html + (list ? "</ul>" : "");
}

function bubble(cls, html) {
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.innerHTML = html;
  el("aiLog").appendChild(div);
  el("aiLog").scrollTop = el("aiLog").scrollHeight;
  return div;
}

function parseSse(chunk, onEvent) {
  for (const block of chunk.split("\n\n")) {
    const ev = /^event: (.+)$/m.exec(block)?.[1];
    const data = /^data: (.+)$/m.exec(block)?.[1];
    if (ev && data) { try { onEvent(ev, JSON.parse(data)); } catch { /* partial */ } }
  }
}

export async function ask(question, focus = "") {
  if (busy || !question.trim()) return;
  if (!takeAsk()) {
    el("aiLog").querySelector(".ai-empty")?.remove();
    bubble("bot", `<div class="body">${esc(asksOutText())} <button type="button" class="ask-link" data-unlock>${esc(t("plan.unlock"))} →</button></div>`);
    return;
  }
  el("aiInput").placeholder = asksLeftText() || t("ai.placeholder");
  busy = true;
  el("aiForm").querySelector("button").disabled = true;
  el("aiLog").querySelector(".ai-empty")?.remove();
  bubble("user", esc(question));
  const bot = bubble("bot", `<div class="steps"></div><div class="body"><span class="muted">${t("ai.thinking")}</span> <span class="typing"><i></i><i></i><i></i></span></div>`);
  const steps = bot.querySelector(".steps");
  const body = bot.querySelector(".body");
  try {
    const res = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, lang: getLang(), view: context.view, id: context.id, page: context.page, focus }),
    });
    if (!res.ok || !res.body) throw new Error(await res.text());
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const cut = buf.lastIndexOf("\n\n");
      if (cut < 0) continue;
      parseSse(buf.slice(0, cut), (ev, d) => {
        if (ev === "tool") steps.insertAdjacentHTML("beforeend", `<span>${t("ai.reading")} ${esc(d.target || d.name)}</span>`);
        if (ev === "answer") {
          if (d.ok && d.text) body.innerHTML = md(d.text);
          else { bot.classList.add("err"); body.textContent = t("ai.error"); }
        }
      });
      buf = buf.slice(cut + 2);
      el("aiLog").scrollTop = el("aiLog").scrollHeight;
    }
  } catch (e) {
    bot.classList.add("err");
    body.textContent = (e && e.message && e.message.length < 140) ? e.message : t("ai.error");
  } finally {
    busy = false;
    el("aiForm").querySelector("button").disabled = false;
  }
}

export function renderAiEmpty() {
  const log = el("aiLog");
  if (!log.querySelector(".msg")) log.innerHTML = `<p class="ai-empty">${esc(t("ai.empty"))}</p>`;
}

export function openDock() {
  el("advisorDock").classList.remove("hidden");
  el("advisorFab").setAttribute("aria-expanded", "true");
  el("advisorFab").classList.add("hidden");
  renderAiEmpty();
  renderContextLine();
}
export function closeDock() {
  el("advisorDock").classList.add("hidden");
  el("advisorFab").setAttribute("aria-expanded", "false");
  el("advisorFab").classList.remove("hidden");
}

/** Open the dock and (optionally) ask straight away — used by every "Ask SMART Advisor" link. */
export function openAi(question = "", focus = "") {
  openDock();
  requestAnimationFrame(() => { if (question) ask(question, focus); else el("aiInput").focus(); });
}

export function initAi() {
  renderSuggestions();
  el("advisorFab").addEventListener("click", () => openAi());
  el("advisorClose").addEventListener("click", closeDock);
  el("aiSuggest").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-q]");
    if (b) ask(t(b.dataset.q));
  });
  el("aiForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = el("aiInput").value;
    el("aiInput").value = "";
    ask(q);
  });
  el("aiInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); el("aiForm").requestSubmit(); }
  });
  document.querySelectorAll("[data-open-advisor]").forEach((b) => b.addEventListener("click", (e) => { e.preventDefault(); openAi(); }));
}
