"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError, pctToDecimal as pct } from "../lib/api";

const num = (v) => (String(v ?? "").trim() === "" ? undefined : String(v).trim().replace(",", "."));
const list = (v) => String(v ?? "").split(/[,\s]+/).filter(Boolean).map((x) => x.replace(",", "."));

const EXAMPLE = {
  instrument_class: "STOCK", sector_kind: "non_financial", market_price: "80", currency: "USD",
  financials_as_of: new Date().toISOString().slice(0, 10), label: "Ejemplo ficticio",
  fcff_base: "100", growth: "0", years: "5", wacc: "10", terminal_growth: "0", net_debt: "0", diluted_shares: "10",
  bear_wacc: "12", bull_growth: "4", ddm_d1: "", ddm_r: "", ddm_g: "",
  rel_method: "pe", rel_metric: "8", rel_multiples: "10, 12, 14",
};

function Field({ label, name, defaults, hint }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input name={name} defaultValue={defaults[name] ?? ""} autoComplete="off" />
      {hint && <small className="muted">{hint}</small>}
    </label>
  );
}

function buildPayload(f) {
  const body = {
    instrument_class: f.get("instrument_class"), sector_kind: f.get("sector_kind"),
    market_price: num(f.get("market_price")), currency: f.get("currency") || "USD",
    financials_as_of: f.get("financials_as_of") || undefined, label: f.get("label") || undefined,
  };
  if (num(f.get("fcff_base"))) {
    const dcf = {
      fcff_base: num(f.get("fcff_base")), growth: pct(num(f.get("growth")) ?? "0"), years: num(f.get("years")),
      wacc: pct(num(f.get("wacc"))), terminal_growth: pct(num(f.get("terminal_growth")) ?? "0"),
      net_debt: num(f.get("net_debt")) ?? "0", diluted_shares: num(f.get("diluted_shares")),
    };
    const scenarios = {};
    if (num(f.get("bear_wacc"))) scenarios.bear = { wacc: pct(num(f.get("bear_wacc"))) };
    if (num(f.get("bull_growth"))) scenarios.bull = { growth: pct(num(f.get("bull_growth"))) };
    if (Object.keys(scenarios).length) dcf.scenarios = scenarios;
    body.dcf = dcf;
  }
  if (num(f.get("ddm_d1"))) {
    body.ddm = { mode: "gordon", d1: num(f.get("ddm_d1")), cost_of_equity: pct(num(f.get("ddm_r"))), growth: pct(num(f.get("ddm_g")) ?? "0") };
  }
  if (num(f.get("rel_metric"))) {
    body.relative = [{ method: f.get("rel_method"), metric: num(f.get("rel_metric")), multiples: list(f.get("rel_multiples")) }];
  }
  return body;
}

const fmt = (v, d = 2) => (v == null ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
const fmtPct = (v) => (v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`);

function Result({ r }) {
  const dcf = r.methods.find((m) => m.method === "dcf" && m.applicable);
  return (
    <section className="stack" aria-label="Resultado">
      <p className="notice" role="note">{r.disclaimer} Guardada como valoración #{r.id} (versión {r.assumptions_version}).</p>
      {!r.evaluable && <p className="notice warn" role="alert">No evaluable con estos supuestos y este tipo de instrumento.</p>}

      <div className="stats">
        <div className="card stat"><span className="label">Precio de mercado</span><strong>{fmt(r.market_price)} {r.currency}</strong></div>
        <div className="card stat">
          <span className="label">Valor central</span>
          <strong>{r.central_value ? fmt(r.central_value) : "Sin valor central"}</strong>
          {!r.compatible && r.evaluable && <span className="sub">Los métodos no son compatibles entre sí</span>}
        </div>
        <div className="card stat">
          <span className="label">Margen de seguridad</span>
          <strong>{fmtPct(r.margin_of_safety_base)}</strong>
          <span className="sub">Potencial hasta el valor: {fmtPct(r.upside_to_value)}</span>
        </div>
        {r.valuation_range && (
          <div className="card stat"><span className="label">Rango entre métodos</span><strong>{fmt(r.valuation_range.low)} – {fmt(r.valuation_range.high)}</strong></div>
        )}
        {r.scenario_range && (
          <div className="card stat">
            <span className="label">Escenarios DCF</span>
            <strong>{fmt(r.scenario_range.bear)} · {fmt(r.scenario_range.base)} · {fmt(r.scenario_range.bull)}</strong>
            <span className="sub">pesimista · base · optimista</span>
          </div>
        )}
        {r.reverse_dcf?.solved && (
          <div className="card stat">
            <span className="label">Crecimiento implícito en el precio</span>
            <strong>{fmtPct(r.reverse_dcf.implied_growth)} anual</strong>
            <span className="sub">DCF inverso del FCFF</span>
          </div>
        )}
      </div>

      <div className="tablewrap">
        <table>
          <caption className="sr">Métodos</caption>
          <thead><tr><th scope="col">Método</th><th scope="col" className="num">Valor por acción</th><th scope="col">Estado</th></tr></thead>
          <tbody>
            {r.methods.map((m) => (
              <tr key={m.method}>
                <th scope="row">{m.method}</th>
                <td className="num">{m.applicable ? fmt(m.value_per_share) : "—"}</td>
                <td>{m.applicable ? "Aplica" : <span className="muted">{m.reason}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {dcf?.sensitivity && (
        <div className="tablewrap">
          <table>
            <caption className="sr">Sensibilidad WACC por crecimiento terminal</caption>
            <thead>
              <tr><th scope="col">WACC \ g</th>{dcf.sensitivity.terminal_growth.map((g) => (<th key={g} scope="col" className="num">{fmtPct(g)}</th>))}</tr>
            </thead>
            <tbody>
              {dcf.sensitivity.wacc.map((w, i) => (
                <tr key={w}>
                  <th scope="row">{fmtPct(w)}</th>
                  {dcf.sensitivity.value_per_share[i].map((v, j) => (<td key={j} className="num">{fmt(v)}</td>))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {r.warnings.length > 0 && (
        <div className="card">
          <h2>Advertencias</h2>
          <ul className="pending-list">{r.warnings.map((w) => (<li key={w}>{w}</li>))}</ul>
        </div>
      )}
    </section>
  );
}

export default function FairValueLab() {
  const router = useRouter();
  const [defaults, setDefaults] = useState({});
  const [formKey, setFormKey] = useState(0);
  const [state, setState] = useState({ status: "idle" });

  async function onSubmit(e) {
    e.preventDefault();
    setState({ status: "loading" });
    try {
      const data = await api("/api/v1/valuations/fair-value", { method: "POST", body: buildPayload(new FormData(e.currentTarget)) });
      setState({ status: "ready", data });
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        router.replace("/login");
        return;
      }
      setState({ status: "error", message: err.message });
    }
  }

  return (
    <main className="wrap">
      <header className="top">
        <div>
          <h1>Fair Value Lab</h1>
          <p className="muted">El valor estimado depende de tus supuestos; no es un precio verdadero ni una predicción.</p>
        </div>
        <nav className="actions" aria-label="Principal">
          <button className="ghost" type="button" onClick={() => { setDefaults(EXAMPLE); setFormKey((k) => k + 1); }}>Cargar ejemplo ficticio</button>
          <Link className="btn ghost" href="/">Portafolio</Link>
        </nav>
      </header>

      <form key={formKey} className="card form" onSubmit={onSubmit}>
        <fieldset>
          <legend>Instrumento</legend>
          <label className="field"><span>Tipo</span>
            <select name="instrument_class" defaultValue={defaults.instrument_class ?? "STOCK"}>
              <option value="STOCK">Acción</option><option value="ADR">ADR</option><option value="ETF">ETF</option><option value="CRYPTO">Cripto</option>
            </select>
          </label>
          <label className="field"><span>Sector</span>
            <select name="sector_kind" defaultValue={defaults.sector_kind ?? "non_financial"}>
              <option value="non_financial">No financiero</option><option value="bank">Banco / financiero</option>
            </select>
          </label>
          <Field label="Precio de mercado" name="market_price" defaults={defaults} />
          <Field label="Moneda" name="currency" defaults={defaults} />
          <Field label="Fecha de estados financieros (AAAA-MM-DD)" name="financials_as_of" defaults={defaults} />
          <Field label="Etiqueta (opcional)" name="label" defaults={defaults} />
        </fieldset>

        <fieldset>
          <legend>DCF (flujo de caja libre a la firma)</legend>
          <Field label="FCFF del último año" name="fcff_base" defaults={defaults} />
          <Field label="Crecimiento anual (%)" name="growth" defaults={defaults} />
          <Field label="Años proyectados" name="years" defaults={defaults} />
          <Field label="WACC (%)" name="wacc" defaults={defaults} />
          <Field label="Crecimiento terminal (%)" name="terminal_growth" defaults={defaults} />
          <Field label="Deuda neta" name="net_debt" defaults={defaults} />
          <Field label="Acciones diluidas" name="diluted_shares" defaults={defaults} />
          <Field label="Escenario pesimista: WACC (%)" name="bear_wacc" defaults={defaults} />
          <Field label="Escenario optimista: crecimiento (%)" name="bull_growth" defaults={defaults} />
        </fieldset>

        <fieldset>
          <legend>DDM (Gordon, opcional)</legend>
          <Field label="Dividendo esperado D1" name="ddm_d1" defaults={defaults} />
          <Field label="Costo de capital (%)" name="ddm_r" defaults={defaults} />
          <Field label="Crecimiento del dividendo (%)" name="ddm_g" defaults={defaults} />
        </fieldset>

        <fieldset>
          <legend>Múltiplos (opcional)</legend>
          <label className="field"><span>Método</span>
            <select name="rel_method" defaultValue={defaults.rel_method ?? "pe"}>
              <option value="pe">P/E</option><option value="p_fcf">P/FCF</option><option value="p_s">P/S</option><option value="p_b">P/B</option>
            </select>
          </label>
          <Field label="Métrica por acción (EPS, FCF, ventas o valor en libros)" name="rel_metric" defaults={defaults} />
          <Field label="Múltiplos de comparables" name="rel_multiples" defaults={defaults} hint="Separados por coma" />
        </fieldset>

        <button type="submit" disabled={state.status === "loading"}>{state.status === "loading" ? "Calculando…" : "Calcular y guardar"}</button>
      </form>

      {state.status === "error" && <p role="alert" className="error">{state.message}</p>}
      {state.status === "ready" && <Result r={state.data} />}
    </main>
  );
}
