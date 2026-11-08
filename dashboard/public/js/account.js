// Account view: customer profile, sites, contracts with current price lists.
import { t, fmt, eur, date, stream, sector } from "./i18n.js";
import { esc, openTrace, ROW_GO } from "./drawer.js";

const el = (id) => document.getElementById(id);

export const initials = (name) =>
  name.split(/\s+/).filter((w) => /[A-Za-zÅÄÖåäö]/.test(w[0])).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";

function termValue(term) {
  const { value, unit } = term;
  if (unit === "%") return `${fmt(value, 1)} %`;
  if (unit === "km") return `${fmt(value)} km`;
  const suffix = unit.replace("EUR", "").replace("/", " / ").trim();
  return `${fmt(value, 2)} €${suffix ? ` ${suffix}` : ""}`;
}

/** Contract drawer: every price term + dates, built as a trace so it reuses the provenance drawer. */
function contractTrace(c) {
  const rows = Object.entries(c.terms).map(([k, v]) => [t(`term.${k}`), termValue(v)]);
  return {
    value: null, unit: "",
    formula: {
      en: `${t("pricing." + c.pricing_model)} · ${c.operator} · start ${c.start} · renews ${c.renewal} · give notice by ${c.notice_by} (${c.notice_days} days)`,
      fi: `${t("pricing." + c.pricing_model)} · ${c.operator} · alkanut ${c.start} · uusiutuu ${c.renewal} · irtisano viimeistään ${c.notice_by} (${c.notice_days} pv)`,
    },
    assumption: t("contract.priceFrom", { date: c.price_from ? date(c.price_from) : "–", reason: t("reason." + c.change_reason), n: c.versions }),
    sources: ["reference/contracts.parquet", "reference/contract_versions.parquet", "reference/devices.parquet"],
    rows: { columns: [t("col.contract"), c.contract_id], rows, total_rows: rows.length },
  };
}

export function renderAccount(p) {
  const a = p.account;
  const ops = [...new Set(a.contracts.map((c) => c.operator))];
  const next = [...a.contracts].sort((x, y) => x.notice_by.localeCompare(y.notice_by))[0];
  const facts = [
    [t("account.profile"), p.customer.name],
    [t("account.sector"), sector(p.customer.sector)],
    [t("account.sites"), fmt(p.customer.n_sites)],
    [t("account.machines"), fmt(p.customer.n_devices)],
    [t("account.operators"), ops.join(", ")],
    [t("account.spend"), eur(p.kpis.total_cost.value)],
    [t("account.nextNotice"), next ? `${date(next.notice_by)} · ${next.operator}` : "–"],
    [t("account.period"), t("top.period")],
    [t("account.asof"), p.period?.as_of ? date(p.period.as_of) : "–"],
  ];
  el("accountFacts").innerHTML = facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");

  el("sitesTable").innerHTML = `<thead><tr><th>${t("col.site")}</th><th>${t("col.municipality")}</th><th class="num">${t("account.machines")}</th>
    <th class="num">${t("col.km")}</th><th>${t("col.hours")}</th></tr></thead><tbody>${a.sites.map((s) => `<tr>
      <td class="strong">${esc(s.name)}<span class="desc">${esc(s.site_id)}</span></td><td>${esc(s.municipality)} · ${esc(s.region)}</td>
      <td class="num">${s.devices}</td><td class="num">${s.round_trip_km} km</td><td>${esc(s.opening_hours)}</td></tr>`).join("")}</tbody>`;

  const soon = new Date(p.period.notice_soon_before); // pipeline-derived from the data's as-of date, not the browser clock
  el("contractsAsOf").textContent = t("asof.data", { date: p.period?.as_of ? date(p.period.as_of) : "–" });
  const tbl = el("contractsTable");
  tbl.innerHTML = `<thead><tr><th>${t("col.contract")}</th><th>${t("col.stream")}</th><th>${t("col.operator")}</th><th>${t("col.pricing")}</th>
    <th class="num">${t("col.liftFee")}</th><th>${t("col.renewal")}</th><th>${t("col.notice")}</th><th class="row-go"></th></tr></thead><tbody>${a.contracts.map((c, i) => `
    <tr class="click" data-i="${i}" tabindex="0">
      <td class="strong">${esc(c.contract_id)}<span class="desc">${esc(c.device_id)} · ${esc(c.site)} · ${fmt(c.volume_m3)} m³</span></td>
      <td>${esc(stream(c.stream))}<span class="desc">${esc(t("policy." + c.collection_policy))}</span></td>
      <td>${esc(c.operator)}</td><td>${esc(t("pricing." + c.pricing_model))}</td>
      <td class="num">${c.terms.lift_fee ? esc(termValue(c.terms.lift_fee)) : "–"}</td>
      <td>${esc(date(c.renewal))}</td>
      <td><span class="state" data-state="${new Date(c.notice_by) < soon ? "bad" : "mute"}">${esc(date(c.notice_by))}</span></td>${ROW_GO}</tr>`).join("")}</tbody>`;
  tbl.querySelectorAll("tr.click").forEach((tr) => {
    const c = a.contracts[Number(tr.dataset.i)];
    const go = () => openTrace(contractTrace(c), `${c.contract_id} · ${stream(c.stream)} · ${c.site}`, `account.contracts.${c.contract_id}`);
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } }); // the drawer takes focus, so Enter must not click on
  });
}
