"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, apiDownload, ApiError, pctToDecimal as pct } from "../lib/api";

const num = (v) => (String(v ?? "").trim() === "" ? undefined : String(v).trim().replace(",", "."));
const list = (v) => String(v ?? "").split(/[,\s]+/).filter(Boolean).map((x) => x.replace(",", "."));
const pctOut = (v) => (v == null ? "—" : `${(Number(v) * 100).toFixed(2)}%`);
const money = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

function Field({ label, name, hint, placeholder }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input name={name} placeholder={placeholder} autoComplete="off" />
      {hint && <small className="muted">{hint}</small>}
    </label>
  );
}

const MODES = {
  historical: { label: "CAGR histórico", url: "/api/v1/calculators/cagr" },
  projected: { label: "CAGR proyectado", url: "/api/v1/calculators/cagr" },
  target: { label: "Precio máximo de compra", url: "/api/v1/calculators/target-buy-price" },
};

function payload(mode, f) {
  if (mode === "historical") {
    return { mode: "historical", initial: num(f.get("initial")), final: num(f.get("final")), years: num(f.get("years")) };
  }
  if (mode === "projected") {
    const body = {
      mode: "projected", buy_price: num(f.get("buy_price")), fundamental_initial: num(f.get("fundamental_initial")),
      growth: pct(num(f.get("growth"))), years: num(f.get("years")), exit_multiple: num(f.get("exit_multiple")),
    };
    if (num(f.get("dilution"))) body.dilution = pct(num(f.get("dilution")));
    if (num(f.get("dividends"))) body.dividends_per_share = list(f.get("dividends"));
    const scenarios = {};
    const bear = {}, bull = {};
    if (num(f.get("bear_growth"))) bear.growth = pct(num(f.get("bear_growth")));
    if (num(f.get("bear_multiple"))) bear.exit_multiple = num(f.get("bear_multiple"));
    if (num(f.get("bull_growth"))) bull.growth = pct(num(f.get("bull_growth")));
    if (num(f.get("bull_multiple"))) bull.exit_multiple = num(f.get("bull_multiple"));
    if (Object.keys(bear).length) scenarios.bear = bear;
    if (Object.keys(bull).length) scenarios.bull = bull;
    if (Object.keys(scenarios).length) body.scenarios = scenarios;
    return body;
  }
  const body = {
    future_value: num(f.get("future_value")), years: num(f.get("years")), target_return: pct(num(f.get("target_return"))),
  };
  if (num(f.get("dividends"))) body.dividends_per_share = list(f.get("dividends"));
  if (num(f.get("estimated_value"))) body.estimated_value = num(f.get("estimated_value"));
  if (num(f.get("margin_of_safety"))) body.margin_of_safety = pct(num(f.get("margin_of_safety")));
  return body;
}

function Stat({ label, value, sub }) {
  return (
    <div className="card stat">
      <span className="label">{label}</span>
      <strong>{value}</strong>
      {sub && <span className="sub">{sub}</span>}
    </div>
  );
}

function Result({ mode, data }) {
  const r = data.result;
  return (
    <section className="stack" aria-label="Resultado">
      {r.scenario_note && <p className="notice warn" role="note">{r.scenario_note}</p>}
      <div className="stats">
        {mode === "historical" && (<><Stat label="CAGR" value={pctOut(r.cagr)} sub="por año, compuesto" /><Stat label="Retorno total" value={pctOut(r.total_return)} /></>)}
        {mode === "projected" && !r.scenarios && (
          <>
            <Stat label="CAGR del precio" value={pctOut(r.cagr_price)} />
            <Stat label="Precio futuro" value={money(r.price_future)} />
            <Stat label="Múltiplo de entrada → salida" value={`${money(r.entry_multiple)} → ${money(r.exit_multiple)}`} sub={`${pctOut(r.multiple_change)} de cambio`} />
            {r.total_irr != null && <Stat label="TIR total (con dividendos)" value={pctOut(r.total_irr)} />}
          </>
        )}
        {mode === "projected" && r.scenarios && Object.entries(r.cagr_price).map(([name, v]) => (
          <Stat key={name} label={`CAGR del precio · ${name}`} value={pctOut(v)} sub={`Precio futuro ${money(r.scenarios[name].price_future)}`} />
        ))}
        {mode === "target" && (
          <>
            <Stat label="Precio máximo por rentabilidad" value={money(r.max_price_by_return)} />
            {r.max_price_by_margin_of_safety && <Stat label="Precio máximo por margen de seguridad" value={money(r.max_price_by_margin_of_safety)} />}
            {r.conservative_max_price && <Stat label="Precio conservador (exige ambos)" value={money(r.conservative_max_price)} />}
          </>
        )}
      </div>
      {(() => {
        const warnings = [...(r.warnings ?? []), ...Object.values(r.scenarios ?? {}).flatMap((s) => s.warnings ?? [])];
        return warnings.length > 0 && (
          <div className="card">
            <h2>Advertencias</h2>
            <ul className="pending-list">{[...new Set(warnings)].map((w) => (<li key={w}>{w}</li>))}</ul>
          </div>
        );
      })()}
    </section>
  );
}

export default function Calculadoras() {
  const router = useRouter();
  const [mode, setMode] = useState("historical");
  const [state, setState] = useState({ status: "idle" });
  const [last, setLast] = useState(null);

  async function onSubmit(e) {
    e.preventDefault();
    setState({ status: "loading" });
    const body = payload(mode, new FormData(e.currentTarget));
    try {
      const data = await api(MODES[mode].url, { method: "POST", body });
      setLast({ mode, body });
      setState({ status: "ready", data });
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        router.replace("/login");
        return;
      }
      setState({ status: "error", message: err.message });
    }
  }

  async function exportCsv() {
    if (!last) return;
    try {
      const blob = await apiDownload(`${MODES[last.mode].url}?format=csv`, last.body);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `hipotesis-${last.mode}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setState({ status: "error", message: err.message });
    }
  }

  return (
    <main className="wrap">
      <header className="top">
        <div>
          <h1>Calculadoras</h1>
          <p className="muted">CAGR y precio máximo de compra. Las proyecciones son escenarios, no pronósticos.</p>
        </div>
        <nav className="actions" aria-label="Principal">
          <Link className="btn ghost" href="/">Portafolio</Link>
        </nav>
      </header>

      <div className="row-actions" role="tablist" aria-label="Calculadora">
        {Object.entries(MODES).map(([key, m]) => (
          <button key={key} role="tab" aria-selected={mode === key} className={mode === key ? "" : "ghost"}
            onClick={() => { setMode(key); setState({ status: "idle" }); }}>{m.label}</button>
        ))}
      </div>

      <form key={mode} className="card form" onSubmit={onSubmit}>
        {mode === "historical" && (
          <fieldset>
            <legend>Sin depósitos ni retiros intermedios</legend>
            <Field label="Valor inicial" name="initial" placeholder="10000" />
            <Field label="Valor final" name="final" placeholder="16105" />
            <Field label="Años" name="years" placeholder="5" />
          </fieldset>
        )}
        {mode === "projected" && (
          <>
            <fieldset>
              <legend>Caso base</legend>
              <Field label="Precio de compra" name="buy_price" />
              <Field label="Fundamental por acción hoy" name="fundamental_initial" hint="EPS, FCF o ventas por acción" />
              <Field label="Crecimiento anual (%)" name="growth" />
              <Field label="Años" name="years" />
              <Field label="Múltiplo de salida" name="exit_multiple" hint="P/E, P/FCF o P/S, igual que el fundamental" />
              <Field label="Dilución anual (%)" name="dilution" hint="Opcional" />
              <Field label="Dividendos por acción, uno por año" name="dividends" hint="Opcional, separados por coma" />
            </fieldset>
            <fieldset>
              <legend>Escenario pesimista (opcional)</legend>
              <Field label="Crecimiento (%)" name="bear_growth" />
              <Field label="Múltiplo de salida" name="bear_multiple" />
            </fieldset>
            <fieldset>
              <legend>Escenario optimista (opcional)</legend>
              <Field label="Crecimiento (%)" name="bull_growth" />
              <Field label="Múltiplo de salida" name="bull_multiple" />
            </fieldset>
          </>
        )}
        {mode === "target" && (
          <fieldset>
            <legend>Precio máximo para una rentabilidad objetivo</legend>
            <Field label="Valor futuro por acción" name="future_value" />
            <Field label="Años" name="years" />
            <Field label="Rentabilidad objetivo anual (%)" name="target_return" />
            <Field label="Dividendos por acción, uno por año" name="dividends" hint="Opcional" />
            <Field label="Valor estimado (Fair Value)" name="estimated_value" hint="Opcional, para margen de seguridad" />
            <Field label="Margen de seguridad (%)" name="margin_of_safety" hint="Opcional" />
          </fieldset>
        )}
        <div className="row-actions">
          <button type="submit" disabled={state.status === "loading"}>{state.status === "loading" ? "Calculando…" : "Calcular"}</button>
          {state.status === "ready" && <button type="button" className="ghost" onClick={exportCsv}>Exportar hipótesis (CSV)</button>}
        </div>
      </form>

      {state.status === "error" && <p role="alert" className="error">{state.message}</p>}
      {state.status === "ready" && <Result mode={mode} data={state.data} />}
    </main>
  );
}
