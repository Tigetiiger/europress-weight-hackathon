// Weight model card: version toggle (v1 → v2 → v3, v4 planned), honest test metrics, inputs, retrain path.
import { t, fmt } from "./i18n.js";
import { esc, openTrace } from "./drawer.js";

const el = (id) => document.getElementById(id);
let current = null;
export const getWeightModel = () => current;

export function renderWeightModel(wm, onChange) {
  if (!current) current = wm.default;
  const v = wm.versions.find((x) => x.id === current) || wm.versions[0];
  el("wmToggle").innerHTML = wm.versions.map((x) => `<button type="button" data-v="${x.id}" aria-pressed="${x.id === current}" ${x.planned ? "disabled" : ""}>${esc(x.id)}${x.planned ? ` · ${esc(t("wm.planned"))}` : ""}</button>`).join("");
  const m = v.test || {};
  el("wmBody").innerHTML = `
    <p class="lead">${esc(t(`wm.${v.id}`))}</p>
    <dl class="stats wm-stats">
      <div><dt>${esc(t("wm.err"))}</dt><dd>${m.median_abs_err_pct != null ? `${fmt(m.median_abs_err_pct, 1)} %` : "–"}</dd></div>
      <div><dt>${esc(t("wm.within"))}</dt><dd>${m.within_10_pct != null ? `${fmt(m.within_10_pct)} %` : "–"}</dd></div>
      <div><dt>${esc(t("wm.bias"))}</dt><dd>${m.bias_pct != null ? `${fmt(m.bias_pct, 1)} %` : "–"}</dd></div>
      <div><dt>${esc(t("wm.n"))}</dt><dd>${m.n != null ? fmt(m.n) : "–"}</dd></div>
    </dl>
    <p class="fine">${esc(t("wm.inputs"))}: ${v.features.map((f) => `<span class="state" data-state="mute">${esc(t(`wmf.${f}`))}</span>`).join(" ")}</p>
    <p class="fine">${esc(t("wm.split", { train: wm.train_period, test: wm.test_period, cal: fmt(wm.calibrated_machines) }))}</p>
    <ol class="ans-steps">${t("wm.retrain").split("|").map((s) => `<li>${esc(s)}</li>`).join("")}</ol>`;
  el("wmToggle").onclick = (e) => {
    const b = e.target.closest("button[data-v]");
    if (!b || b.disabled) return;
    current = b.dataset.v;
    renderWeightModel(wm, onChange);
    onChange?.(current);
  };
  el("wmBody").onclick = (e) => { if (e.target.closest(".wm-stats")) openTrace(wm.trace, t("wm.title"), "weight_model"); };
}
