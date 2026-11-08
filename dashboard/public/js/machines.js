// Machine drawings: a compactor outline filled to its current level, with "full in N days".
// Forecast and weight are DEMO placeholders — production values come from Europress' SMART forecast and the weight model.
import { t, fmt, date, stream, getLang } from "./i18n.js";
import { esc, chip, bindChips, openExplain, tx } from "./drawer.js";
import { STREAM_COLORS } from "./charts.js";
import { openAi } from "./ask.js";
import { addStrings } from "./i18n.js";
import { mountShaderFill } from "./shader/runtime.js";
import { weightsNow, cardWeightLink, hasWeightModel } from "./sensor/customer.js";
import { isFree } from "./plan.js";
import { setup as gradientSetup, render as gradientRender, manifest as gradientManifest } from "./shader/moving-gradient.js";

/** "Ask the SMART assistant" fill — parameters exactly as in the Figma design (node 16:343). */
const ASK_SHADER = {
  setup: gradientSetup, render: gradientRender, manifest: gradientManifest,
  params: {
    intensity: 4.289999961853027,
    gradient: { stops: [
      { position: 0, color: { r: 0.8470588326454163, g: 0.9058823585510254, b: 1, a: 0.4699999988079071 } },
      { position: 0.5, color: { r: 0.3294117748737335, g: 0.8705882430076599, b: 0.6627451181411743, a: 0.47999998927116394 } },
      { position: 1, color: { r: 0.7215686440467834, g: 0.5372549295425415, b: 0.29019609093666077, a: 0.6600000262260437 } },
    ] },
    gradientBalance: 0, material: 0, morphSpeed: 3.740000009536743, detail: 1.7799999713897705, twist: 0.03999999910593033,
    zoom: 72, gradientMethod: 0, warp: 0.25999999046325684, rotationSpeed: 12,
  },
};

addStrings({
  en: {
    "rt.up": "Status: up", "rt.noSignal": "No signal",
    "rt.fullNow": "Full now", "rt.fullTomorrow": "Full tomorrow ({date})", "rt.fullIn": "Full in {n} days ({date})",
    "rt.ordered": "Truck ordered for {date}", "rt.willOrder": "SMART orders the truck on {date}", "rt.willOrderToday": "SMART orders the truck today",
    "rt.timetable": "Next truck {date} (timetable)",
    "rt.weight": "≈ {kg} kg inside now", "rt.noModel": "Weight: model coming for this waste type", "rt.weightSmart": "Live weight in SMART", "rt.ask": "Ask the SMART assistant",
    "rt.how.save": "How do I save money here?", "rt.how.mixed": "How do I cut mixed waste?",
    "rt.q.save": "My machine {name} ({id}) is {fill} % full. How do I save money with it? Give me 3 simple steps.",
    "rt.q.mixed": "My mixed-waste machine {name} ({id}) is {fill} % full. How do I put less in it and save money? Give me 3 simple steps.",
    "rt.q.saveFree": "How do I save money with my machine {name} ({id})? Give me 3 simple steps.",
    "rt.q.mixedFree": "How do I put less in my mixed-waste machine {name} ({id}) and save money? Give me 3 simple steps.",
  },
  fi: {
    "rt.up": "Tila: toiminnassa", "rt.noSignal": "Ei yhteyttä",
    "rt.fullNow": "Täynnä nyt", "rt.fullTomorrow": "Täynnä huomenna ({date})", "rt.fullIn": "Täynnä {n} päivässä ({date})",
    "rt.ordered": "Auto tilattu: {date}", "rt.willOrder": "SMART tilaa auton {date}", "rt.willOrderToday": "SMART tilaa auton tänään",
    "rt.timetable": "Seuraava auto {date} (aikataulu)",
    "rt.weight": "≈ {kg} kg sisällä nyt", "rt.noModel": "Paino: malli tulossa tälle jätelajille", "rt.weightSmart": "Reaaliaikainen paino SMARTissa", "rt.ask": "Kysy SMART-avustajalta",
    "rt.how.save": "Miten säästän tässä?", "rt.how.mixed": "Miten vähennän sekajätettä?",
    "rt.q.save": "Laitteeni {name} ({id}) on {fill} % täynnä. Miten säästän sen avulla? Anna 3 helppoa vaihetta.",
    "rt.q.mixed": "Sekajätelaitteeni {name} ({id}) on {fill} % täynnä. Miten laitan siihen vähemmän ja säästän? Anna 3 helppoa vaihetta.",
    "rt.q.saveFree": "Miten säästän laitteellani {name} ({id})? Anna 3 helppoa vaihetta.",
    "rt.q.mixedFree": "Miten laitan sekajätelaitteeseeni {name} ({id}) vähemmän ja säästän? Anna 3 helppoa vaihetta.",
  },
});

const PICKUP_LINE = 85; // SMART orders the pickup automatically here — the drawing never fills above it
// What each stream means for the customer: recyclables earn a credit, mixed waste is the one to eliminate.
const KIND = { cardboard: "credit", plastic_film: "credit", mixed_plastic: "credit", paper: "recycle", biowaste: "bio", mixed_waste: "mixed" };
const KIND_STATE = { credit: "good", recycle: "info", bio: "note", mixed: "warn" };

/**
 * Side view of a Europress CombiMax-type compactor (drawn from product photos), filled to its current level.
 * Front → back: hook-lift A-frame · press unit with sloped hopper lid and control panel · tall collar frame ·
 * container with top/bottom bands, rear door, hinges and lock · skid rail with brackets and rollers.
 * Container length follows the machine's volume so 3 m³ and 12 m³ units read differently.
 */
export function compactorSvg(m, { label = true } = {}) {
  const f = isFree() ? 0 : Math.max(0, Math.min(PICKUP_LINE, m.fill_now ?? 0)); // Free: no fill level
  const color = STREAM_COLORS[m.stream] || "currentColor";
  const v = m.volume_m3 ?? 8;
  const L = Math.round(Math.min(150, Math.max(84, 56 + v * 9)));
  const P0 = 18, C0 = 90, C1 = C0 + L;                 // press start, container start/end
  const top = 26, rise = 4, bot = 98;                  // container roof rises toward the rear
  const ox = Math.round((250 - (C1 + 12)) / 2);        // centre the drawing in the card
  const level = bot - ((bot - top) * f) / 100;
  const target = bot - (bot - top) * (PICKUP_LINE / 100);
  const id = `clip-${String(m.device_id).replace(/[^A-Za-z0-9_-]/g, "")}`;
  const shell = `M${C0} ${top}L${C1 - 7} ${top - rise}L${C1} ${top - rise + 7}V${bot}H${C0}Z`;
  const brackets = Array.from({ length: Math.floor((C1 - P0) / 26) }, (_, i) =>
    `<rect x="${P0 + 8 + i * 26}" y="${bot + 6}" width="7" height="3" class="mc-dark"/>`).join("");
  return `<svg class="mc-svg" viewBox="0 0 250 118" ${label ? `role="img" aria-label="${esc(m.device_id)}${isFree() ? "" : ` ${fmt(f)} %`}"` : 'aria-hidden="true"'}>
    <defs><clipPath id="${id}"><path d="${shell}"/></clipPath>
      <pattern id="${id}-h" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" class="mc-hatch"/></pattern></defs>
    <g transform="translate(${ox} 0)">
      <!-- underframe: skid rail, brackets, hook-lift rollers -->
      <rect x="${P0 - 10}" y="${bot}" width="${C1 + 4 - (P0 - 10)}" height="6" class="mc-press"/>
      ${brackets}
      <circle cx="${P0 - 5}" cy="${bot + 9}" r="5" class="mc-dark"/><circle cx="${C1 - 2}" cy="${bot + 9}" r="5" class="mc-dark"/>
      <!-- hook-lift A-frame with hook -->
      <rect x="${P0 - 10}" y="52" width="10" height="${bot - 52}" class="mc-press"/>
      <path d="M${P0 - 6.5} 52v-5a4 4 0 1 1 8 0v3" class="mc-line"/>
      <!-- press unit: sloped hopper lid, service door, control panel with E-stop -->
      <path d="M${P0} ${bot}V60L${C0 - 10} 46V${bot}Z" class="mc-press"/>
      <path d="M${P0} 59.5L${C0 - 10} 44.5" class="mc-lid"/>
      <rect x="${P0 + 5}" y="65" width="14" height="27" rx="1" class="mc-line"/>
      <rect x="${P0 + 26}" y="63" width="25" height="14" rx="2" class="mc-dark"/>
      <circle cx="${P0 + 32}" cy="70" r="2.6" class="mc-socket"/><circle cx="${P0 + 44}" cy="70" r="3.2" class="mc-estop"/>
      <!-- container fill level + SMART target -->
      <g clip-path="url(#${id})">
        <rect x="${C0}" y="${level}" width="${L}" height="${bot - level}" class="mc-fill" style="fill:${color}"/>
        ${m.stream === "mixed_waste" ? `<rect x="${C0}" y="${level}" width="${L}" height="${bot - level}" fill="url(#${id}-h)"/>` : ""}
      </g>
      ${isFree() ? "" : `<line x1="${C0}" x2="${C1}" y1="${target}" y2="${target}" class="mc-pickup"/>`}
      <!-- container shell, bands, rear door, hinges, lock -->
      <path d="${shell}" class="mc-body"/>
      <path d="M${C0} ${top + 7}L${C1 - 7} ${top - rise + 7}H${C1}" class="mc-line"/>
      <line x1="${C0}" x2="${C1}" y1="${bot - 9}" y2="${bot - 9}" class="mc-line"/>
      <line x1="${C1 - 15}" x2="${C1 - 15}" y1="${top - rise + 2}" y2="${bot}" class="mc-line"/>
      <rect x="${C1 - 1}" y="34" width="4" height="7" class="mc-dark"/><rect x="${C1 - 1}" y="62" width="4" height="7" class="mc-dark"/><rect x="${C1 - 1}" y="84" width="4" height="7" class="mc-dark"/>
      <rect x="${C1 - 11}" y="55" width="6" height="11" rx="1" class="mc-line"/>
      <!-- collar frame between press and container (stands above both) -->
      <rect x="${C0 - 10}" y="14" width="10" height="${bot - 14}" class="mc-press"/>
    </g>
  </svg>`;
}

const TRUCK_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 6.5h11v9h-11zM13.5 9.5h4l3 3v3h-7"/><circle cx="6.5" cy="17" r="1.8"/><circle cx="17" cy="17" r="1.8"/></svg>';
const loc = () => (getLang() === "fi" ? "fi-FI" : "en-GB");
const fmtDay = (iso) => new Intl.DateTimeFormat(loc(), { weekday: "short", day: "numeric", month: "short" }).format(new Date(`${iso}T12:00:00`));
let asOf = null; // status date of the data ("today" in the demo)
const dayDate = (iso) => (iso === asOf ? t("m.today") : fmtDay(iso));
const weekday = (i) => new Intl.DateTimeFormat(loc(), { weekday: "long" }).format(new Date(2024, 0, 1 + i)); // 2024-01-01 = Monday

function scheduleText(m) {
  const days = String(m.schedule_weekdays || "").split(",").filter((x) => x.trim() !== "").map((x) => weekday(Number(x)));
  const list = days.join(t("sched.and"));
  const step = Math.max(1, Math.round(days.length / (m.schedule_per_week || days.length || 1)));
  if (step === 1) return t("sched.every", { days: list });
  if (step === 2) return t("sched.everyOther", { days: list });
  return t("sched.everyN", { n: step, days: list });
}

const visitFill = (m) => (m.visit_fill_pct > PICKUP_LINE ? `≥ ${PICKUP_LINE}` : `≈ ${fmt(m.visit_fill_pct)}`);
const opName = (m) => m.operator || m.operator_id || "";
const isEarly = (m) => m.policy !== "ON_CALL" && m.visit_fill_pct != null && m.visit_fill_pct < 60;

/** Who sends the truck and when, as plain-text lines: SMART on-call order, or the operator's fixed timetable. */
function truckLines(m) {
  const op = opName(m);
  if (isFree() && m.policy === "ON_CALL") return [t("plan.manual", { op })];
  if (m.policy !== "ON_CALL") {
    return [t("truck.fixed", { op, sched: scheduleText(m) }),
      ...(m.truck_on ? [isFree() ? t("rt.timetable", { date: dayDate(m.truck_on) }) : t("truck.next", { date: dayDate(m.truck_on), fill: visitFill(m) })] : [])];
  }
  const first = m.pickup_ordered ? t("truck.ordered", { op })
    : m.order_on ? t(m.pickup_reason === "interval" ? "truck.willOrderInterval" : "truck.willOrder", { op, date: dayDate(m.order_on), thr: fmt(m.order_threshold_pct) })
    : t("m.notSoon");
  return [first, ...(m.truck_on ? [t("truck.eta", { date: dayDate(m.truck_on), h: fmt(m.lead_h) })] : [])];
}

function truckState(m) {
  if (isFree()) return "mute";
  if (isEarly(m)) return "warn";
  return m.policy === "ON_CALL" && (m.pickup_ordered || (m.days_to_pickup ?? 99) <= 2) ? "info" : "mute";
}

const tip = (text) => `<p class="mc-tip"><span class="state" data-state="warn">${esc(text)}</span></p>`;
function truckBlock(m) {
  const lowThr = !isFree() && m.policy === "ON_CALL" && m.stream !== "biowaste" && m.order_threshold_pct != null && m.order_threshold_pct < 75;
  return `<div class="mc-truck" data-state="${truckState(m)}">
      <span class="mc-truck-ic">${TRUCK_ICON}</span>
      <div>${truckLines(m).map((l, i) => `<p class="${i ? "mc-truck-sub" : "mc-truck-main"}">${esc(l)}</p>`).join("")}
        ${!isFree() && isEarly(m) ? tip(t("truck.early")) : ""}
        ${lowThr ? tip(t("truck.thr", { thr: fmt(m.order_threshold_pct) })) : ""}
        ${m.operator_on_time_pct != null ? `<p class="mc-truck-sub">${esc(t("truck.onTime", { op: opName(m), pct: fmt(m.operator_on_time_pct) }))}</p>` : ""}
      </div></div>`;
}

function typeTag(m) {
  const kind = KIND[m.stream] || "recycle";
  return `<span class="state" data-state="${KIND_STATE[kind]}">${esc(t(`kind.${kind}`))}</span>`;
}
/** Weight now from the soft sensor (the one weight source); filled in after the model service answers. */
let sensorKg = new Map();
const modelKg = (m) => sensorKg.get(m.device_id) ?? null;
const weightSlot = (m) => (isFree() ? `<span class="fine">🔒 ${esc(t("rt.weightSmart"))}</span>` : hasWeightModel(m.stream) ? `<span data-w="${esc(m.device_id)}">${modelKg(m) != null ? esc(t("rt.weight", { kg: fmt(modelKg(m)) })) : "…"}</span>` : `<span class="fine">${esc(t("rt.noModel"))}</span>`);
const weightLine = (m) => weightSlot(m);
const mixedAsk = (m) => (KIND[m.stream] === "mixed" ? `<button type="button" class="ask-link" data-mixed="${esc(m.device_id)}">${esc(t("m.askMixed"))}</button>` : "");
const howScheduled = (m) => (isFree() && m.policy === "ON_CALL" ? t("plan.manualShort") : m.policy === "ON_CALL" ? t("mv.onCall", { thr: fmt(m.order_threshold_pct) }) : t("mv.timetable", { sched: scheduleText(m) }));

/** Fill now, as an explainable value (opens the machine drawer). Free: no fill level at all. */
const fillChip = (m) => (isFree() ? `<span class="fine">🔒 ${esc(t("plan.lock.fill"))}</span>` : chip(`${fmt(m.fill_now)}&nbsp;%`, `${m.device_id}: ${fmt(m.fill_now)} %`, m.device_id));

// ---------------------------------------------------------------- real-time card (Figma "Article" 1:489)
const SPARKLE = '<img class="rt-spark" src="/img/figma/cc0e4.svg" width="176" height="20" alt="">';

/** "Cardboard 1", "Cardboard 2" … when a customer has several machines of one stream. */
export function machineNames(rows) {
  const total = {}, seen = {};
  rows.forEach((m) => { total[m.stream] = (total[m.stream] || 0) + 1; });
  return new Map(rows.map((m) => {
    seen[m.stream] = (seen[m.stream] || 0) + 1;
    return [m.device_id, total[m.stream] > 1 ? `${stream(m.stream)} ${seen[m.stream]}` : stream(m.stream)];
  }));
}

/** Two plain lines: when it is full, and what happens with the truck. */
function plainNext(m) {
  if (isFree()) return [m.policy !== "ON_CALL" && m.truck_on ? t("rt.timetable", { date: dayDate(m.truck_on) }) : t("plan.manual", { op: opName(m) })];
  const days = Math.round(m.days_to_full ?? 99);
  const full = (m.fill_now ?? 0) >= (m.order_threshold_pct ?? PICKUP_LINE) || m.full_on === asOf ? t("rt.fullNow")
    : !m.full_on ? t("m.notSoon")
    : days <= 1 ? t("rt.fullTomorrow", { date: fmtDay(m.full_on) })
    : t("rt.fullIn", { n: fmt(days), date: fmtDay(m.full_on) });
  const truck = m.pickup_ordered ? t("rt.ordered", { date: dayDate(m.truck_on || m.pickup_on) })
    : m.policy !== "ON_CALL" && m.truck_on ? t("rt.timetable", { date: dayDate(m.truck_on) })
    : m.order_on === asOf ? t("rt.willOrderToday")
    : m.order_on ? t("rt.willOrder", { date: fmtDay(m.order_on) }) : "";
  return [full, truck].filter(Boolean);
}

const askQuestion = (m, name) => (isFree() ? t(KIND[m.stream] === "mixed" ? "rt.q.mixedFree" : "rt.q.saveFree", { name, id: m.device_id }) : t(KIND[m.stream] === "mixed" ? "rt.q.mixed" : "rt.q.save", { name, id: m.device_id, fill: fmt(m.fill_now) }));

function rtCard(m, name, { full = false } = {}) {
  const kind = KIND[m.stream] || "recycle";
  const up = m.fill_sensor_pct != null;
  const kg = modelKg(m);
  return `<article class="rt" data-kind="${kind}">
    <header class="rt-top">
      <p class="rt-name"><b>${esc(name)}</b><span>${esc(m.device_id)} · ${fmt(m.volume_m3)} m³</span></p>
      <p class="rt-status" data-up="${up}">${esc(t(up ? "rt.up" : "rt.noSignal"))}<i aria-hidden="true"></i></p>
    </header>
    <div class="rt-fig">${compactorSvg(m)}</div>
    <div class="rt-fill">${fillChip(m)}${typeTag(m)}</div>
    ${hasWeightModel(m.stream) || isFree()
      ? `<div class="rt-wrow"><p class="rt-weight">${weightSlot(m)}</p>${isFree() ? "" : cardWeightLink(m.device_id)}</div>`
      : `<p class="rt-weight rt-nomodel">${esc(t("rt.noModel"))}</p>`}
    <div class="rt-next">${TRUCK_ICON}<div>${plainNext(m).map((l) => `<p>${esc(l)}</p>`).join("")}</div></div>
    ${isFree() && m.policy === "ON_CALL" ? manualOrder(m) : ""}
    ${full && m.operator ? `<p class="rt-op">${esc(howScheduled(m))} · ${esc(m.operator)}${m.operator_on_time_pct != null ? ` · ${esc(t("mv.onTime", { pct: fmt(m.operator_on_time_pct) }))}` : ""}</p>` : ""}
    <button type="button" class="rt-ask" data-ask="${esc(m.device_id)}">
      <canvas class="rt-shader" aria-hidden="true"></canvas>${SPARKLE}
      <span class="rt-ask-text"><b>${esc(t("rt.ask"))}</b><span>${esc(t(kind === "mixed" ? "rt.how.mixed" : "rt.how.save"))}</span></span>
    </button>
  </article>`;
}

/** Free: the customer orders the truck by hand (demo: the request is only shown, not sent). */
const manualOrder = (m) => `<p class="rt-order"><button type="button" class="btn ghost" data-order="${esc(m.device_id)}">${esc(t("plan.order"))}</button></p>`;

/** "compact": Overview card · "design": drawing cards · "mixed": small drawing + info table per machine · "table": plain table. */
const VIEW = {
  compact: (rows, names) => rows.map((m) => rtCard(m, names.get(m.device_id))).join(""),
  design: (rows, names) => rows.map((m) => rtCard(m, names.get(m.device_id), { full: true })).join(""),
  mixed: (rows) => rows.map((m) => `<article class="mrow" data-kind="${KIND[m.stream] || "recycle"}">
      <div class="mrow-fig">${compactorSvg(m)}<div class="mrow-fill">${fillChip(m)}</div></div>
      <dl class="mrow-dl">
        <div><dt>${t("col.machine")}</dt><dd>${esc(m.device_id)} · ${esc(m.site)}</dd></div>
        <div><dt>${t("col.stream")}</dt><dd><span class="dot" style="background:${STREAM_COLORS[m.stream]}"></span>${esc(stream(m.stream))} · ${fmt(m.volume_m3)} m³ ${typeTag(m)}</dd></div>
        <div><dt>${t("mv.scheduling")}</dt><dd>${esc(howScheduled(m))}</dd></div>
        <div><dt>${t("col.operator")}</dt><dd>${esc(m.operator || "–")}${m.operator_on_time_pct != null ? ` · ${t("mv.onTime", { pct: fmt(m.operator_on_time_pct) })}` : ""}</dd></div>
        <div class="span2"><dt>${t("mv.nextTruck")}</dt><dd>${truckBlock(m)}</dd></div>
        <div><dt>${t("mv.weight")}</dt><dd>${weightLine(m)}</dd></div>
        <div><dt>${t("mv.fillRate")}</dt><dd>${isFree() ? `🔒 ${esc(t("plan.lock.fill"))}` : m.fill_rate_per_day != null ? t("mv.perDay", { v: fmt(m.fill_rate_per_day, 1) }) : "–"}</dd></div>
      </dl>
      ${mixedAsk(m)}
    </article>`).join(""),
  table: (rows) => `<div class="table-wrap"><table class="table"><thead><tr>
      <th>${t("col.machine")}</th><th>${t("col.stream")}</th><th class="num">${t("mv.fillNow")}</th><th>${t("mv.scheduling")}</th>
      <th>${t("col.operator")}</th><th>${t("mv.nextTruck")}</th><th class="num">${t("mv.fillAtVisit")}</th><th class="num">${t("mv.weight")}</th></tr></thead><tbody>${
    rows.map((m) => `<tr>
      <td class="strong">${esc(m.device_id)}<span class="desc">${esc(m.site)} · ${fmt(m.volume_m3)} m³</span></td>
      <td><span class="dot" style="background:${STREAM_COLORS[m.stream]}"></span>${esc(stream(m.stream))}</td>
      <td class="num">${fillChip(m)}</td>
      <td>${esc(howScheduled(m))}</td>
      <td>${esc(m.operator || "–")}</td>
      <td>${isFree() ? (m.policy !== "ON_CALL" && m.truck_on ? esc(dayDate(m.truck_on)) : esc(t("plan.manualShort"))) : m.pickup_ordered ? esc(t("mv.ordered")) : m.truck_on ? esc(dayDate(m.truck_on)) : "–"}</td>
      <td class="num">${isFree() ? "🔒" : m.visit_fill_pct == null ? "–" : m.visit_fill_pct > PICKUP_LINE ? `≥ ${PICKUP_LINE} %` : `${fmt(m.visit_fill_pct)} %`}</td>
      <td class="num">${weightSlot(m)}</td></tr>`).join("")}</tbody></table></div>`,
};

/** Machine drawer: fill now, what it means, and the few facts that matter. */
function openMachine(m) {
  const facts = [
    [t("m.facts.stream"), `${stream(m.stream)} · ${fmt(m.volume_m3)} m³`],
    [t("m.facts.next"), truckLines(m).join(" · ")],
    ...(m.operator_on_time_pct != null ? [[t("m.facts.ontime"), `${opName(m)} · ${fmt(m.operator_on_time_pct)} %`]] : []),
    ...(modelKg(m) != null ? [[t("m.facts.weight"), `≈ ${fmt(modelKg(m))} kg`]] : []),
  ];
  openExplain({ title: `${m.device_id} · ${stream(m.stream)}`, value: `${fmt(m.fill_now)} %`, what: tx("x.machine.fill"), facts, focus: `machines.${m.device_id}` });
}

export function renderMachinesNow(container, list, { limit = Infinity, mode = "design" } = {}) {
  asOf = list[0]?.as_of ?? null;
  const rows = [...list].sort((a, b) => (a.days_to_pickup ?? 999) - (b.days_to_pickup ?? 999)).slice(0, limit);
  const names = machineNames([...list].sort((a, b) => String(a.device_id).localeCompare(String(b.device_id))));
  container.dataset.mode = mode;
  container.innerHTML = (VIEW[mode] || VIEW.design)(rows, names);
  bindChips(container, (id) => openMachine(rows.find((m) => m.device_id === id)));
  container.querySelectorAll("[data-mixed]").forEach((b) =>
    b.addEventListener("click", () => openAi(t("q.mixedMachine", { id: b.dataset.mixed }), `machines.${b.dataset.mixed}`)));
  container.querySelectorAll("button[data-ask]").forEach((b) => {
    const m = rows.find((x) => x.device_id === b.dataset.ask);
    b.addEventListener("click", () => openAi(askQuestion(m, names.get(m.device_id)), `machines.${m.device_id}`));
  });
  container.querySelectorAll("button[data-order]").forEach((b) => b.addEventListener("click", () => {
    const m = rows.find((x) => x.device_id === b.dataset.order);
    b.closest(".rt-order").innerHTML = `<span class="state" data-state="info">✓ ${esc(t("plan.ordered", { op: opName(m) }))}</span>`;
  }));
  container.querySelectorAll("canvas.rt-shader").forEach((c) => mountShaderFill(c, ASK_SHADER));
  // the soft sensor fills every weight slot once the model service answers
  if (container.querySelector("[data-w]")) {
    weightsNow(list).then((w) => {
      sensorKg = new Map([...w].map(([id, row]) => [id, row.mass_kg]));
      container.querySelectorAll("[data-w]").forEach((el) => {
        const kgNow = sensorKg.get(el.dataset.w);
        el.textContent = kgNow != null ? t("rt.weight", { kg: fmt(kgNow) }) : "–";
      });
    }).catch(() => container.querySelectorAll("[data-w]").forEach((el) => { el.textContent = "–"; }));
  }
}
