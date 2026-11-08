// Soft-sensor data access + small shared helpers (formatting in Europe/Helsinki, materials).
import { t, getLang, fmt } from "../i18n.js";
import { STREAM_COLORS } from "../charts.js";

export const TZ = "Europe/Helsinki";
export const MATERIALS = ["cardboard", "paper", "plastic_film", "mixed_waste"];
export const MAT_COLORS = { ...STREAM_COLORS };
export const material = (m) => t(`ss.mat.${m}`);

const locale = () => (getLang() === "fi" ? "fi-FI" : "en-GB");
/** "Tue 4 Aug, 14:00" in Helsinki time. */
export const fmtTime = (ms) => new Intl.DateTimeFormat(locale(), {
  timeZone: TZ, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
}).format(new Date(ms));
export const fmtDay = (ms) => new Intl.DateTimeFormat(locale(), { timeZone: TZ, day: "numeric", month: "short" }).format(new Date(ms));
export const kg = (v) => (v == null ? "–" : `${fmt(v)} kg`);
export const pct = (v, d = 1) => (v == null ? "–" : `${fmt(v, d)} %`);
export const since = (h) => (h == null ? "–" : h < 48 ? t("ss.hours", { n: fmt(h) }) : t("ss.days", { n: fmt(h / 24, 1) }));

/** GET /api/sensor/<path>; resolves `data` of the {success, data, error} envelope. */
export async function sensorGet(path, params = {}, signal) {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null));
  const res = await fetch(`/api/sensor/${path}${q.size ? `?${q}` : ""}`, { signal });
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok || !body?.success) {
    const err = new Error(body?.error || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return body.data;
}

const staticCache = new Map();
/** Static JSON written by pipeline/softsensor_data.py. */
export async function sensorStatic(name) {
  if (!staticCache.has(name)) {
    staticCache.set(name, fetch(`/data/softsensor/${name}.json`).then((r) => {
      if (!r.ok) throw new Error(`${name}.json: ${r.status}`);
      return r.json();
    }).catch((e) => { staticCache.delete(name); throw e; }));
  }
  return staticCache.get(name);
}
