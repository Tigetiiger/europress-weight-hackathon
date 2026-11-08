// Entry point for the soft-sensor section (Europress workspace): tabs, notice, dispatch to the four pages.
import "./strings.js";
import { t } from "../i18n.js";
import { esc } from "../drawer.js";
import { renderLive, stopLive } from "./live.js";
import { renderCheck } from "./check.js";
import { renderModel } from "./model.js";
import { renderHow } from "./how.js";

export const SENSOR_VIEWS = ["ep-sensor", "ep-sensor-check", "ep-sensor-model", "ep-sensor-how"];
const TABS = [["ep-sensor", "ss.tab.live"], ["ep-sensor-check", "ss.tab.check"], ["ep-sensor-model", "ss.tab.model"], ["ep-sensor-how", "ss.tab.how"]];
const RENDER = { "ep-sensor-check": renderCheck, "ep-sensor-model": renderModel, "ep-sensor-how": renderHow };

export const isSensorView = (view) => SENSOR_VIEWS.includes(view);

function top(view) {
  return `<p class="ss-notice" role="note"><span class="state" data-state="note">${esc(t("top.synthetic"))}</span>${esc(t("ss.notice"))}</p>
    <nav class="ss-tabs" aria-label="${esc(t("nav.ep.sensor"))}">${TABS.map(([v, key]) =>
      `<a href="#${v}"${v === view ? ' aria-current="page"' : ""}>${esc(t(key))}</a>`).join("")}</nav>`;
}

/** Render a soft-sensor page into its section. `arg` is the machine id for "#ep-sensor/BIN-0001". */
export async function renderSensor(view, arg) {
  const section = document.getElementById(`view-${view}`);
  if (!section) return;
  section.querySelector("[data-ss-top]").innerHTML = top(view);
  const body = section.querySelector("[data-ss-body]");
  if (view === "ep-sensor") return renderLive(body, arg && /^BIN-\d{4}$/.test(arg) ? arg : null);
  stopLive();
  return RENDER[view](body);
}

export { stopLive };
