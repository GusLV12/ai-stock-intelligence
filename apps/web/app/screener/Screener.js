"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, apiDownloadGet, apiUpload, ApiError, pctToDecimal as pct } from "../lib/api";
import { MotionPanel } from "../lib/Motion";
import "./screener.css";

const num = (v) => (String(v ?? "").trim() === "" ? undefined : String(v).trim().replace(",", "."));
const fmtPct = (v) => (v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`);
const fmtNum = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 }));

const TEMPLATE =
  "ticker,fiscal_year,period_end,published_at,currency,revenue,gross_profit,operating_income,net_income,cfo,capex,ebitda,total_assets,current_assets,current_liabilities,long_term_debt,total_debt,cash,shares_outstanding\n" +
  "AAA,2025,2025-12-31,2026-02-20,USD,1200,400,,100,150,30,,1100,500,300,250,300,80,100\n";

const FILTER_LABELS = {
  min_fscore: "F-Score mínimo",
  min_margin_of_safety: "Margen de seguridad",
  require_positive_fcf: "FCF positivo",
  max_debt_to_assets: "Deuda sobre activos",
  min_revenue_growth: "Crecimiento de ventas",
  require_fresh_data: "Datos vigentes",
};

function Badge({ rating }) {
  return <span className="badge" title={rating.basis}>{rating.label}</span>;
}

function payload(f) {
  const body = {};
  if (num(f.get("min_fscore"))) body.min_fscore = Number(num(f.get("min_fscore")));
  if (f.get("allow_partial_fscore")) body.allow_partial_fscore = true;
  if (num(f.get("min_margin_of_safety"))) body.min_margin_of_safety = pct(num(f.get("min_margin_of_safety")));
  if (f.get("require_positive_fcf")) body.require_positive_fcf = true;
  if (num(f.get("max_debt_to_assets"))) body.max_debt_to_assets = pct(num(f.get("max_debt_to_assets")));
  if (num(f.get("min_revenue_growth"))) body.min_revenue_growth = pct(num(f.get("min_revenue_growth")));
  if (f.get("require_fresh_data")) body.require_fresh_data = true;
  if (num(f.get("as_of"))) body.as_of = num(f.get("as_of"));
  return body;
}

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export default function Screener() {
  const router = useRouter();
  const [state, setState] = useState({ status: "idle" });
  const [showAll, setShowAll] = useState(false);
  const [upload, setUpload] = useState({ status: "idle" });

  function onAuthError(err) {
    if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
      router.replace("/login");
      return true;
    }
    return false;
  }

  async function onRun(e) {
    e.preventDefault();
    setState({ status: "loading" });
    try {
      const data = await api("/api/v1/screeners/fair-value", { method: "POST", body: payload(new FormData(e.currentTarget)) });
      setState({ status: "ready", data });
    } catch (err) {
      if (!onAuthError(err)) setState({ status: "error", message: err.message });
    }
  }

  async function onUpload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUpload({ status: "loading" });
    try {
      const r = await apiUpload("/api/v1/fundamentals/import", file);
      setUpload({ status: "ok", message: `Importado: ${r.created} nuevos, ${r.new_versions} reexpresiones, ${r.unchanged} sin cambio.` });
    } catch (err) {
      if (!onAuthError(err)) setUpload({ status: "error", message: err.message });
    }
    e.target.value = "";
  }

  async function downloadXlsxTemplate() {
    try {
      downloadBlob(await apiDownloadGet("/api/v1/fundamentals/import?format=xlsx"), "plantilla-estados-financieros.xlsx");
    } catch (err) {
      setUpload({ status: "error", message: err.message });
    }
  }

  async function exportResults(format) {
    try {
      downloadBlob(await apiDownloadGet(`/api/v1/screeners/runs/${state.data.id}?format=${format}`), `screener-${state.data.id}.${format}`);
    } catch (err) {
      setState({ status: "error", message: err.message });
    }
  }

  const data = state.status === "ready" ? state.data : null;
  const rows = data ? (showAll ? data.rows : data.rows.filter((r) => r.passed)) : [];

  return (
    <main className="wrap screener-page">
      <header className="top">
        <div>
          <h1>Screener</h1>
          <p className="muted">Lista de empresas para INVESTIGAR. No es una recomendación de compra.</p>
        </div>
        <nav className="actions" aria-label="Principal">
          <Link className="btn ghost" href="/fair-value">Fair Value</Link>
          <Link className="btn ghost" href="/">Portafolio</Link>
        </nav>
      </header>

      <div className="screener-layout">
      <div className="screener-sidebar card">
      <section className="screener-data stack" aria-label="Datos financieros">
        <h2>Datos financieros</h2>
        <p className="muted">Carga estados financieros anuales en CSV o Excel (.xlsx) con las columnas de la plantilla. El XLSX de posiciones de GBM no contiene estos estados. También puedes obtener datos de la SEC con ingest_sec_financials (ver docs/runbooks/sec-edgar.md). Los datos faltantes no se convierten en cero.</p>
        <div className="row-actions">
          <label className="btn screener-upload">
            Subir CSV o Excel
            <input className="screener-file" type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={onUpload} />
          </label>
          <button type="button" className="ghost" onClick={() => downloadBlob(new Blob([TEMPLATE], { type: "text/csv" }), "plantilla-estados-financieros.csv")}>
            Plantilla CSV
          </button>
          <button type="button" className="ghost" onClick={downloadXlsxTemplate}>Plantilla Excel</button>
        </div>
        {upload.status === "loading" && <p role="status">Importando…</p>}
        {upload.status === "ok" && <p role="status">{upload.message}</p>}
        {upload.status === "error" && <p role="alert" className="error">{upload.message}</p>}
      </section>

      <form className="screener-filters form" onSubmit={onRun}>
        <fieldset>
          <legend>Filtros (todos opcionales)</legend>
          <label className="field"><span>F-Score mínimo (0–9)</span><input name="min_fscore" placeholder="7" /></label>
          <label className="field"><span>Margen de seguridad mínimo (%)</span><input name="min_margin_of_safety" placeholder="25" />
            <small className="muted">Usa tu última valoración guardada y el precio actual</small></label>
          <label className="field"><span>Deuda sobre activos máxima (%)</span><input name="max_debt_to_assets" placeholder="50" /></label>
          <label className="field"><span>Crecimiento de ventas mínimo (%)</span><input name="min_revenue_growth" placeholder="5" /></label>
          <label className="field"><span>Datos hasta la fecha (AAAA-MM-DD)</span><input name="as_of" placeholder="hoy" />
            <small className="muted">Solo usa lo que ya era público ese día</small></label>
        </fieldset>
        <fieldset>
          <legend>Opciones</legend>
          <label className="check"><input type="checkbox" name="require_positive_fcf" /> Exigir flujo de caja libre positivo</label>
          <label className="check"><input type="checkbox" name="require_fresh_data" /> Exigir estados financieros vigentes (≤ 15 meses)</label>
          <label className="check"><input type="checkbox" name="allow_partial_fscore" /> Aceptar F-Score parcial (algún criterio sin dato)</label>
        </fieldset>
        <button type="submit" disabled={state.status === "loading"}>{state.status === "loading" ? "Evaluando…" : "Ejecutar y guardar"}</button>
      </form>
      </div>

      <div className="screener-main">
      {state.status === "error" && <p role="alert" className="error">{state.message}</p>}
      {state.status === "idle" && <section className="card screener-initial"><h2>Resultados del screener</h2><p className="muted">Configura los filtros y ejecuta una corrida para ver el universo, la cobertura y las empresas evaluadas.</p></section>}
      {state.status === "loading" && <section className="card screener-initial" role="status">Evaluando emisoras y guardando la corrida…</section>}

      {data && (
        <MotionPanel className="stack screener-results" role="region" aria-label="Resultados">
          <h2>Resultados del screener</h2>
          <p className="notice" role="note">
            {data.disclaimer} Corrida #{data.id} guardada ({data.model_version}). {data.consensus.reason}
          </p>
          <div className="stats screener-stats">
            <div className="card stat"><span className="label">Universo (acciones y ADR)</span><strong>{data.coverage.universe}</strong></div>
            <div className="card stat"><span className="label">Con estados financieros</span><strong>{data.coverage.with_financials}</strong></div>
            <div className="card stat"><span className="label">Aprobadas</span><strong>{data.coverage.passed}</strong>
              <span className="sub">{data.candidates.join(", ") || "ninguna"}</span></div>
            <div className="card stat"><span className="label">No aprobadas</span><strong>{data.rows.filter((r) => !r.passed).length}</strong></div>
              <div className="card stat"><span className="label">Sin datos financieros</span>
                <strong>{data.coverage.without_financials.length}</strong><span className="sub">{data.coverage.without_financials.join(", ")}</span></div>
          </div>

          <div className="row-actions screener-results-actions">
            <label className="check"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Mostrar también las no aprobadas</label>
            <div className="row-actions"><button type="button" className="ghost" onClick={() => exportResults("csv")}>Exportar CSV</button><button type="button" className="ghost" onClick={() => exportResults("xlsx")}>Exportar Excel</button></div>
          </div>

          {rows.length === 0 ? (
            <div className="card empty"><h2>Sin resultados</h2><p>Ninguna empresa cumple todos los filtros con los datos disponibles.</p></div>
          ) : (
            <div className="tablewrap">
              <table>
                <caption className="sr">Resultados del screener</caption>
                <thead>
                  <tr>
                    <th scope="col">Emisora</th><th scope="col">Estado</th><th scope="col" className="num">F-Score</th>
                    <th scope="col" className="num">Margen</th><th scope="col" className="num">FCF</th>
                    <th scope="col">Valoración</th><th scope="col">Calidad</th><th scope="col">Crecim.</th>
                    <th scope="col">Riesgo</th><th scope="col">Datos</th><th scope="col">Por qué</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.ticker}>
                      <th scope="row">{r.ticker}</th>
                      <td><span className={`screener-status ${r.passed ? "is-passed" : "is-failed"}`}>{r.passed ? "Aprobada" : "No aprobada"}</span></td>
                      <td className="num">{r.fscore.score ?? "—"}{r.fscore.status === "parcial" ? ` /${r.fscore.evaluable_criteria}` : ""}</td>
                      <td className="num">{fmtPct(r.metrics.margin_of_safety)}</td>
                      <td className="num">{fmtNum(r.metrics.free_cash_flow)}</td>
                      <td><Badge rating={r.ratings.valuation} /></td>
                      <td><Badge rating={r.ratings.financial_quality} /></td>
                      <td><Badge rating={r.ratings.growth} /></td>
                      <td><Badge rating={r.ratings.risk} /></td>
                      <td><Badge rating={r.ratings.data_quality} /></td>
                      <td>
                        {r.failed_filters.length > 0 && <div>Falla: {r.failed_filters.map((f) => FILTER_LABELS[f] ?? f).join(", ")}</div>}
                        {r.missing_data_filters.length > 0 && <div className="muted">Sin dato: {r.missing_data_filters.map((f) => FILTER_LABELS[f] ?? f).join(", ")}</div>}
                        {r.passed && <span className="muted">FY{r.data_refs.fiscal_year} v{r.data_refs.version} · {r.data_refs.source === "SEC_EDGAR" ? "SEC" : r.data_refs.source}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </MotionPanel>
      )}
      </div>
      </div>
    </main>
  );
}
