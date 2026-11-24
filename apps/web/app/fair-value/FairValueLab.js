"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, pctToDecimal as pct } from "../lib/api";
import { MotionPanel } from "../lib/Motion";
import "./fair-value.css";

const num = (v) => (String(v ?? "").trim() === "" ? undefined : String(v).trim().replace(",", "."));
const list = (v) => String(v ?? "").split(/[,\s]+/).filter(Boolean).map((x) => x.replace(",", "."));

const EXAMPLE = {
  instrument_class: "STOCK", sector_kind: "non_financial", market_price: "80", currency: "USD",
  financials_as_of: "2025-12-31", label: "Ejemplo ficticio",
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
    market_price: num(f.get("market_price")), currency: f.get("currency") || undefined,
    financials_as_of: f.get("financials_as_of") || undefined, label: f.get("label") || undefined,
  };
  if (num(f.get("fcff_base"))) {
    const dcf = {
      fcff_base: num(f.get("fcff_base")), growth: pct(num(f.get("growth"))), years: num(f.get("years")),
      wacc: pct(num(f.get("wacc"))), terminal_growth: pct(num(f.get("terminal_growth"))),
      net_debt: num(f.get("net_debt")) ?? "", diluted_shares: num(f.get("diluted_shares")),
    };
    const scenarios = {};
    if (num(f.get("bear_wacc"))) scenarios.bear = { wacc: pct(num(f.get("bear_wacc"))) };
    if (num(f.get("bull_growth"))) scenarios.bull = { growth: pct(num(f.get("bull_growth"))) };
    if (Object.keys(scenarios).length) dcf.scenarios = scenarios;
    body.dcf = dcf;
  }
  if (num(f.get("ddm_d1"))) {
    body.ddm = { mode: "gordon", d1: num(f.get("ddm_d1")), cost_of_equity: pct(num(f.get("ddm_r"))), growth: pct(num(f.get("ddm_g"))) };
  }
  if (num(f.get("rel_metric"))) {
    body.relative = [{ method: f.get("rel_method"), metric: num(f.get("rel_metric")), multiples: list(f.get("rel_multiples")) }];
  }
  return body;
}

const fmt = (v, d = 2) => (v == null ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
const fmtPct = (v) => (v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`);

function Result({ r, saved }) {
  const dcf = r.methods.find((m) => m.method === "dcf" && m.applicable);
  return (
    <section className="stack" aria-label="Resultado">
      <p className="notice" role="note">{r.disclaimer} {saved ? `Guardada como valoración #${r.id}` : "Cálculo sin guardar"} (versión {r.assumptions_version ?? r.model_version}).</p>
      {!r.evaluable && <p className="notice warn" role="alert">No evaluable con estos supuestos y este tipo de instrumento.</p>}

      <div className="stats">
        <div className="card stat"><span className="label">Precio de mercado</span><strong>{fmt(r.market_price)} {r.currency}</strong></div>
        <div className="card stat">
          <span className="label">Valor central</span>
          <strong>{r.central_value != null ? fmt(r.central_value) : "Sin valor central"}</strong>
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
  const [mode, setMode] = useState("guided");
  const [step, setStep] = useState(0);
  const [instruments, setInstruments] = useState([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  const [local, setLocal] = useState(null);
  const [history, setHistory] = useState([]);
  const [notice, setNotice] = useState("");
  const [priceMode, setPriceMode] = useState("manual");
  const [lastPayload, setLastPayload] = useState(null);
  const requestId = useRef(0);

  async function loadHistory() {
    try { setHistory(await api("/api/v1/valuations/fair-value")); }
    catch (err) { setNotice(`No se pudo cargar el historial: ${err.message}`); }
  }
  useEffect(() => {
    let live = true;
    api("/api/v1/instruments").then((data) => { if (live) setInstruments(Array.isArray(data) ? data : data.results ?? []); })
      .catch((err) => { if (live) setNotice(`No se pudieron cargar las emisoras: ${err.message}`); });
    api("/api/v1/valuations/fair-value").then((data) => { if (live) setHistory(data); })
      .catch((err) => { if (live) setNotice(`No se pudo cargar el historial: ${err.message}`); });
    return () => { live = false; };
  }, []);

  async function chooseInstrument(instrument) {
    const id = ++requestId.current;
    setSelected(instrument); setQuery(""); setLocal({ loading: true }); setState({ status: "idle" });
    const [priceResult, financialResult] = await Promise.allSettled([
      api(`/api/v1/market/prices?instrument=${instrument.id}`), api(`/api/v1/fundamentals/${instrument.id}`),
    ]);
    if (id !== requestId.current) return;
    const price = priceResult.status === "fulfilled" ? priceResult.value : null;
    const financials = financialResult.status === "fulfilled" ? financialResult.value : null;
    setLocal({ price, financials, priceError: priceResult.status === "rejected" && priceResult.reason.status !== 404 ? priceResult.reason.message : "", financialError: financialResult.status === "rejected" ? financialResult.reason.message : "" });
    setPriceMode(price ? "registered" : "manual");
    setDefaults({ instrument_class: instrument.asset_class, sector_kind: instrument.sector_kind || "non_financial", currency: price?.currency || instrument.currency, market_price: price?.price || "", label: instrument.name });
    setFormKey((k) => k + 1);
  }

  async function onSubmit(e) {
    e.preventDefault();
    setState({ status: "loading" });
    try {
      const body = buildPayload(new FormData(e.currentTarget));
      if (!body.currency || !/^[A-Za-z]{3}$/.test(body.currency)) throw new Error("Indica una moneda de tres letras, por ejemplo USD.");
      if (selected) body.instrument = selected.id;
      if (selected && priceMode === "registered" && local?.price) {
        delete body.market_price;
        body.expected_price_observed_at = local.price.observed_at;
        body.expected_market_price = local.price.price;
        body.expected_price_provider = local.price.provider;
      }
      const data = await api("/api/v1/valuations/fair-value/preview", { method: "POST", body });
      setLastPayload(body); setState({ status: "ready", data, saved: false }); setStep(4);
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        router.replace("/login");
        return;
      }
      setState({ status: "error", message: err.message });
    }
  }

  async function save() {
    if (!lastPayload || state.status !== "ready" || state.saved) return;
    setState((s) => ({ ...s, saving: true }));
    try {
      const data = await api("/api/v1/valuations/fair-value", { method: "POST", body: lastPayload });
      setState({ status: "ready", data, saved: true }); await loadHistory();
    } catch (err) { setState({ status: "error", message: err.message }); }
  }

  async function openHistory(id) {
    try {
      const detail = await api(`/api/v1/valuations/${id}`);
      setLastPayload(null); setState({ status: "ready", data: { ...detail.result, id: detail.id }, saved: true, detail }); setStep(4);
    } catch (err) { setNotice(`No se pudo abrir la valoración: ${err.message}`); }
  }
  const matches = query.trim() ? instruments.filter((i) => `${i.name} ${(i.identifiers ?? []).map((x) => x.value).join(" ")}`.toLowerCase().includes(query.trim().toLowerCase())).slice(0, 12) : [];

  return (
    <main className="wrap">
      <header className="top">
        <div>
          <h1>Fair Value Lab</h1>
          <p className="muted">El valor estimado depende de tus supuestos; no es un precio verdadero ni una predicción.</p>
        </div>
        <nav className="actions" aria-label="Principal">
          <button className="ghost" type="button" disabled={state.status === "loading" || state.saving} onClick={() => { requestId.current++; setSelected(null); setLocal(null); setPriceMode("manual"); setState({ status: "idle" }); setDefaults(EXAMPLE); setFormKey((k) => k + 1); }}>Cargar ejemplo ficticio</button>
          <Link className="btn ghost" href="/">Portafolio</Link>
        </nav>
      </header>

      {notice && <p role="alert" className="notice warn">{notice}</p>}
      {defaults.label === "Ejemplo ficticio" && !selected && <p className="notice" role="note">Datos de ejemplo: no corresponden a una valoración real.</p>}
      <div className="fv-modes" aria-label="Modo de trabajo">
        <button type="button" aria-pressed={mode === "guided"} className={mode === "guided" ? "" : "ghost"} onClick={() => setMode("guided")}>Modo guiado</button>
        <button type="button" aria-pressed={mode === "advanced"} className={mode === "advanced" ? "" : "ghost"} onClick={() => setMode("advanced")}>Modo avanzado</button>
      </div>
      <section className="fv-search card" aria-labelledby="fv-search-title">
        <div><h2 id="fv-search-title">1. Buscar y cargar una acción</h2><p className="muted">Busca por símbolo o nombre entre las emisoras registradas; también puedes capturar los datos manualmente.</p>
          <label className="field"><span>Buscar acción por símbolo o nombre</span><input type="search" disabled={state.status === "loading" || state.saving} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Ej. AAPL" /></label>
          {query.trim() && <ul className="fv-matches">{matches.map((i) => <li key={i.id}><span><strong>{i.identifiers?.find((x) => x.kind === "TICKER")?.value || "Sin símbolo"}</strong> · {i.name} · {i.market || "Mercado no indicado"} · {i.currency}</span><button type="button" className="ghost" disabled={state.status === "loading" || state.saving} onClick={() => chooseInstrument(i)}>Cargar acción</button></li>)}{!matches.length && <li>Sin resultados locales.</li>}</ul>}
        </div>
        <aside className="fv-source"><h3>Acción seleccionada</h3>
          {!selected ? <p className="muted">Ninguna. Puedes usar captura manual.</p> : <><strong>{selected.name}</strong><p className="muted">{selected.market || "Mercado no indicado"} · {selected.currency}</p>
            {local?.loading ? <p role="status">Consultando datos locales…</p> : <><p>Precio: {local?.price ? `${local.price.price} ${local.price.currency}` : "No disponible"}</p>
              {local?.price && <p className="muted small">Fuente: {local.price.provider} · {new Date(local.price.observed_at).toLocaleString("es-MX")} · {local.price.age_hours} h</p>}
              <p>Estados financieros: {local?.financials?.length ? `${local.financials.length} registro(s)` : "No disponibles"}</p>
              {local?.financials?.slice(0, 3).map((f) => <p className="muted small" key={`${f.fiscal_year}-${f.version}`}>Ejercicio {f.fiscal_year}, versión {f.version} · {f.currency} · {f.source} · cierre {f.period_end} · publicado {f.published_at || "sin fecha"}</p>)}
              {local?.financials?.[0] && <details><summary>Consultar datos del último estado</summary><p className="muted small">Fuente: {local.financials[0].source} · {local.financials[0].currency}. Son datos de referencia; no se copian automáticamente a los supuestos.</p><dl>{[["Ingresos", "revenue"], ["Flujo operativo", "cfo"], ["Inversión de capital", "capex"], ["Flujo libre (CFO − CAPEX)", "free_cash_flow"], ["Deuda total", "total_debt"], ["Efectivo", "cash"], ["Acciones", "shares_outstanding"]].map(([label, key]) => <div key={key}><dt>{label}</dt><dd>{local.financials[0][key] ?? "No disponible"}</dd></div>)}</dl>{local.financials[0].currency !== selected.currency && <p className="notice warn">La moneda del estado financiero difiere de la del instrumento; verifica los datos antes de usarlos.</p>}</details>}
              {(local?.priceError || local?.financialError) && <p role="alert" className="notice warn">No se pudieron consultar todos los datos: {local.priceError || local.financialError}</p>}
            </>}</>}
        </aside>
      </section>
      {mode === "guided" && <nav className="fv-steps" aria-label="Pasos de valoración">{["Instrumento", "DCF", "DDM", "Múltiplos", "Resultados"].map((name, i) => <button key={name} type="button" className={step === i ? "" : "ghost"} aria-current={step === i ? "step" : undefined} onClick={() => setStep(i)}>{i + 1}. {name}</button>)}</nav>}
      <form key={formKey} className="card form" onSubmit={onSubmit} onChange={(e) => { if (e.target.name === "market_price") setPriceMode("manual"); setLastPayload(null); setState({ status: "idle" }); }}>
        <fieldset className="fv-controls" disabled={state.status === "loading" || state.saving}>
        <fieldset hidden={mode === "guided" && step !== 0}>
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
          <Field label={priceMode === "registered" ? "Precio registrado (editar para captura manual)" : "Precio de mercado manual"} name="market_price" defaults={defaults} />
          {selected && local?.price && <label className="field"><span>Procedencia del precio</span><select value={priceMode} onChange={(e) => { setPriceMode(e.target.value); if (e.target.value === "registered") e.target.form.elements.market_price.value = local.price.price; }}><option value="registered">Último precio registrado</option><option value="manual">Captura manual</option></select></label>}
          <Field label="Moneda" name="currency" defaults={defaults} />
          <Field label="Fecha de estados financieros (AAAA-MM-DD)" name="financials_as_of" defaults={defaults} />
          <Field label="Etiqueta (opcional)" name="label" defaults={defaults} />
        </fieldset>

        <fieldset hidden={mode === "guided" && step !== 1}>
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

        <fieldset hidden={mode === "guided" && step !== 2}>
          <legend>DDM (Gordon, opcional)</legend>
          <Field label="Dividendo esperado D1" name="ddm_d1" defaults={defaults} />
          <Field label="Costo de capital (%)" name="ddm_r" defaults={defaults} />
          <Field label="Crecimiento del dividendo (%)" name="ddm_g" defaults={defaults} />
        </fieldset>

        <fieldset hidden={mode === "guided" && step !== 3}>
          <legend>Múltiplos (opcional)</legend>
          <label className="field"><span>Método</span>
            <select name="rel_method" defaultValue={defaults.rel_method ?? "pe"}>
              <option value="pe">P/E</option><option value="p_fcf">P/FCF</option><option value="p_s">P/S</option><option value="p_b">P/B</option>
            </select>
          </label>
          <Field label="Métrica por acción (EPS, FCF, ventas o valor en libros)" name="rel_metric" defaults={defaults} />
          <Field label="Múltiplos de comparables" name="rel_multiples" defaults={defaults} hint="Separados por coma" />
        </fieldset>

        <div className="actions"><button type="submit" disabled={state.status === "loading" || local?.loading}>{state.status === "loading" ? "Calculando…" : "Calcular sin guardar"}</button>
          <button type="button" className="ghost" onClick={save} disabled={state.status !== "ready" || state.saved || state.saving || !lastPayload}>{state.saving ? "Guardando…" : "Guardar valoración"}</button></div>
        </fieldset>
      </form>

      {state.status === "error" && <p role="alert" className="error">{state.message}</p>}
      {state.status === "ready" && <MotionPanel><Result r={state.data} saved={state.saved} />{state.detail && <details><summary>Supuestos guardados</summary><pre className="fv-inputs">{JSON.stringify(state.detail.inputs, null, 2)}</pre></details>}</MotionPanel>}
      <section className="card fv-history"><h2>Historial de valoraciones</h2>{history.length ? <ul>{history.map((v) => <li key={v.id}><span>{v.label || `Valoración #${v.id}`} · {new Date(v.created_at).toLocaleString("es-MX")} · {v.model_version} · {v.central_value == null ? "Sin valor central" : fmt(v.central_value)}</span><button className="ghost" type="button" disabled={state.status === "loading" || state.saving} onClick={() => openHistory(v.id)}>Ver detalle</button></li>)}</ul> : <p className="muted">Aún no hay valoraciones guardadas.</p>}</section>
    </main>
  );
}
