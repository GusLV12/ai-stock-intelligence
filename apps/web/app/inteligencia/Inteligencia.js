"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";

const TABS = [
  ["exposure", "Exposición"],
  ["rebalance", "Rebalanceo"],
  ["shocks", "Shocks"],
  ["risk", "Riesgo"],
  ["theses", "Tesis"],
];

const num = (v) => (String(v ?? "").trim() === "" ? undefined : String(v).trim().replace(",", "."));
const pct = (v, d = 1) => (v == null ? "—" : `${(Number(v) * 100).toFixed(d)}%`);
const pctRaw = (v, d = 2) => (v == null ? "—" : `${Number(v).toFixed(d)}%`);
const usd = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US", { style: "currency", currency: "USD" }));

function useGuard() {
  const router = useRouter();
  return useCallback(
    (err) => {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        router.replace("/login");
        return true;
      }
      return false;
    },
    [router],
  );
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

function Notes({ items }) {
  const list = (items ?? []).filter(Boolean);
  if (!list.length) return null;
  return (
    <div className="card">
      <h2>Avisos</h2>
      <ul className="pending-list">{list.map((w) => (<li key={w}>{w}</li>))}</ul>
    </div>
  );
}

function WeightTable({ title, groups }) {
  return (
    <div className="tablewrap">
      <table>
        <caption className="sr">{title}</caption>
        <thead><tr><th scope="col">{title}</th><th scope="col" className="num">Valor</th><th scope="col" className="num">Peso</th></tr></thead>
        <tbody>
          {Object.entries(groups).map(([k, v]) => (
            <tr key={k}><th scope="row">{k}</th><td className="num">{usd(v.value)}</td><td className="num">{pct(v.weight)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------ Exposición ------------------------------ */
function Exposure() {
  const guard = useGuard();
  const [state, setState] = useState({ status: "loading" });
  const [saved, setSaved] = useState("");

  const fetchExposure = useCallback(async () => {
    try {
      return { status: "ready", data: await api("/api/v1/portfolio/exposure") };
    } catch (err) {
      return guard(err) ? null : { status: "error", message: err.message };
    }
  }, [guard]);
  useEffect(() => {
    let alive = true;
    fetchExposure().then((r) => { if (alive && r) setState(r); });
    return () => { alive = false; };
  }, [fetchExposure]);

  async function savePolicy(e) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body = { name: f.get("name") || "Política", objective: f.get("objective") };
    if (num(f.get("max_position_pct"))) body.max_position_pct = num(f.get("max_position_pct"));
    if (num(f.get("min_cash_pct"))) body.min_cash_pct = num(f.get("min_cash_pct"));
    const cls = {};
    for (const part of String(f.get("max_class_pct") || "").split(",").map((x) => x.trim()).filter(Boolean)) {
      const [k, v] = part.split(":");
      if (k && v) cls[k.trim().toUpperCase()] = v.trim();
    }
    if (Object.keys(cls).length) body.max_class_pct = cls;
    try {
      await api("/api/v1/portfolio/policies", { method: "POST", body });
      setSaved("Política guardada.");
      fetchExposure().then((r) => r && setState(r));
    } catch (err) {
      if (!guard(err)) setSaved(err.message);
    }
  }

  if (state.status === "loading") return <p role="status">Cargando…</p>;
  if (state.status === "error") return <p role="alert" className="error">{state.message}</p>;
  const d = state.data;
  if (!d.evaluable) return <div className="card empty"><h2>Sin posiciones valoradas</h2><p>{d.note}</p></div>;
  const c = d.concentration;
  return (
    <div className="stack">
      <div className="stats">
        <Stat label="Valor total" value={usd(d.total_value_usd)} sub="incluye efectivo" />
        <Stat label="Posición mayor" value={`${c.largest.ticker} ${pct(c.largest.weight)}`} sub={`Top 3: ${pct(c.top3_weight)} · Top 5: ${pct(c.top5_weight)}`} />
        <Stat label="Posiciones efectivas" value={Number(c.effective_positions).toFixed(1)} sub={`de ${c.position_count} · HHI ${Number(c.hhi).toFixed(3)}`} />
        <Stat label="Conciliación" value={d.reconciliation.difference ? `Dif. ${usd(d.reconciliation.difference)}` : "—"} sub={d.reconciliation.comparable ? "coincide con el dashboard" : "no comparable (monedas mixtas o posiciones fuera)"} />
      </div>
      {d.breaches.length > 0 && (
        <div className="card">
          <h2>Incumplimientos de tu política</h2>
          <ul className="pending-list">
            {d.breaches.map((b) => (
              <li key={`${b.type}-${b.subject}`}>
                {b.type === "min_cash" ? "Efectivo por debajo del mínimo" : b.type === "position" ? `Posición ${b.subject} sobre el límite` : `Clase ${b.subject} sobre el límite`}:
                {" "}{pctRaw(b.weight_pct)} (límite {pctRaw(b.limit_pct)}; exceso {pctRaw(b.excess_pct)})
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="grid2">
        <WeightTable title="Por emisora" groups={Object.fromEntries(d.positions.map((p) => [p.ticker, p]))} />
        <WeightTable title="Por clase de activo" groups={d.by_asset_class} />
        <WeightTable title="Por tipo de sector" groups={d.by_sector_kind} />
        <WeightTable title="Por moneda" groups={d.by_currency} />
      </div>
      <Notes items={[...(d.warnings ?? []), d.etf_look_through?.note, d.disclaimer]} />

      <form className="card form" onSubmit={savePolicy}>
        <fieldset>
          <legend>Política (se guarda una versión nueva)</legend>
          <label className="field"><span>Nombre</span><input name="name" placeholder="Prudente" /></label>
          <label className="field"><span>Objetivo</span>
            <select name="objective" defaultValue="mixed"><option value="growth">Crecimiento</option><option value="dividends">Dividendos</option><option value="mixed">Mixta</option></select></label>
          <label className="field"><span>Máximo por posición (%)</span><input name="max_position_pct" placeholder="25" /></label>
          <label className="field"><span>Efectivo mínimo (%)</span><input name="min_cash_pct" placeholder="5" /></label>
          <label className="field"><span>Máximo por clase</span><input name="max_class_pct" placeholder="CRYPTO:10, ETF:30" /></label>
        </fieldset>
        <div className="row-actions"><button type="submit">Guardar política</button>{saved && <span role="status">{saved}</span>}</div>
      </form>
    </div>
  );
}

/* ------------------------------ Rebalanceo ------------------------------ */
function Rebalance() {
  const guard = useGuard();
  const [state, setState] = useState({ status: "idle" });

  async function run(e) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body = { allow_sells: !!f.get("allow_sells") };
    if (num(f.get("contribution"))) body.contribution = num(f.get("contribution"));
    if (num(f.get("commission"))) body.commission_per_trade = num(f.get("commission"));
    const targets = {};
    for (const part of String(f.get("targets") || "").split(",").map((x) => x.trim()).filter(Boolean)) {
      const [k, v] = part.split(":");
      if (k && v) targets[k.trim().toUpperCase()] = v.trim();
    }
    if (Object.keys(targets).length) body.targets = targets;
    setState({ status: "loading" });
    try {
      setState({ status: "ready", data: await api("/api/v1/portfolio/rebalance-preview", { method: "POST", body }) });
    } catch (err) {
      if (!guard(err)) setState({ status: "error", message: err.message });
    }
  }

  const d = state.status === "ready" ? state.data : null;
  return (
    <div className="stack">
      <form className="card form" onSubmit={run}>
        <fieldset>
          <legend>Pesos objetivo (deja vacío para usar tu política)</legend>
          <label className="field"><span>Objetivos (%)</span><input name="targets" placeholder="AAA:40, BNK:20, GLD:20" /></label>
          <label className="field"><span>Aportación nueva (USD)</span><input name="contribution" placeholder="200" /></label>
          <label className="field"><span>Comisión por operación (USD)</span><input name="commission" placeholder="0" /></label>
          <label className="check"><input type="checkbox" name="allow_sells" /> Permitir ventas (por defecto solo se compra con la aportación)</label>
        </fieldset>
        <button type="submit" disabled={state.status === "loading"}>{state.status === "loading" ? "Calculando…" : "Calcular propuesta"}</button>
      </form>
      {state.status === "error" && <p role="alert" className="error">{state.message}</p>}
      {d && (
        <>
          <p className="notice warn" role="note">{d.disclaimer} Objetivos de: {d.targets_source}.</p>
          <div className="stats">
            <Stat label="Compras" value={usd(d.summary.buys_usd)} />
            <Stat label="Ventas" value={usd(d.summary.sells_usd)} />
            <Stat label="Efectivo después" value={usd(d.summary.cash_after_usd)} sub={`objetivo ${usd(d.summary.cash_target_usd)}`} />
            <Stat label="Desviación residual máx." value={pctRaw(d.summary.max_residual_drift_pct)} />
          </div>
          <div className="tablewrap">
            <table>
              <caption className="sr">Operaciones teóricas</caption>
              <thead><tr><th scope="col">Emisora</th><th scope="col">Acción</th><th scope="col" className="num">Títulos</th><th scope="col" className="num">Monto</th><th scope="col" className="num">Peso actual</th><th scope="col" className="num">Objetivo</th><th scope="col" className="num">Después</th></tr></thead>
              <tbody>
                {d.trades.map((t) => (
                  <tr key={t.ticker}>
                    <th scope="row">{t.ticker}</th>
                    <td>{t.side === "BUY" ? "Comprar" : t.side === "SELL" ? "Vender" : "—"}</td>
                    <td className="num">{t.side ? Number(t.shares).toString() : "—"}</td>
                    <td className="num">{t.side ? usd(t.amount) : "—"}</td>
                    <td className="num">{pctRaw(t.current_weight_pct, 1)}</td>
                    <td className="num">{t.target_weight_pct == null ? "sin objetivo" : pctRaw(t.target_weight_pct, 1)}</td>
                    <td className="num">{t.post_weight_pct == null ? "—" : pctRaw(t.post_weight_pct, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Notes items={d.warnings} />
        </>
      )}
    </div>
  );
}

/* -------------------------------- Shocks -------------------------------- */
function Shocks() {
  const guard = useGuard();
  const [presets, setPresets] = useState([]);
  const [state, setState] = useState({ status: "idle" });

  useEffect(() => {
    api("/api/v1/portfolio/scenarios/presets").then(setPresets).catch((err) => guard(err));
  }, [guard]);

  async function run(e) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body = {};
    if (f.get("preset")) body.preset = f.get("preset");
    if (num(f.get("target")) && num(f.get("pct"))) body.shocks = [{ target: num(f.get("target")), pct: num(f.get("pct")) }];
    if (num(f.get("fx"))) body.fx_usd_mxn_pct = num(f.get("fx"));
    setState({ status: "loading" });
    try {
      setState({ status: "ready", data: await api("/api/v1/portfolio/scenarios", { method: "POST", body }) });
    } catch (err) {
      if (!guard(err)) setState({ status: "error", message: err.message });
    }
  }

  const d = state.status === "ready" ? state.data : null;
  return (
    <div className="stack">
      <form className="card form" onSubmit={run}>
        <fieldset>
          <legend>Escenario determinista</legend>
          <label className="field"><span>Preset</span>
            <select name="preset" defaultValue=""><option value="">— ninguno —</option>{presets.map((p) => (<option key={p.id} value={p.id}>{p.label}</option>))}</select></label>
          <label className="field"><span>Shock propio: objetivo</span><input name="target" placeholder="AAA, class:CRYPTO o all" /></label>
          <label className="field"><span>Variación (%)</span><input name="pct" placeholder="-15" /></label>
          <label className="field"><span>USD/MXN (%)</span><input name="fx" placeholder="5" /></label>
        </fieldset>
        <button type="submit" disabled={state.status === "loading"}>Simular</button>
      </form>
      {state.status === "error" && <p role="alert" className="error">{state.message}</p>}
      {d && (
        <>
          <div className="stats">
            <Stat label="Antes" value={usd(d.before_usd)} />
            <Stat label="Después" value={usd(d.after_usd)} sub={`${usd(d.change_usd)} (${pctRaw(d.change_pct)})`} />
            {d.mxn_view && (
              <Stat label="En MXN" value={Number(d.mxn_view.change_mxn).toLocaleString("es-MX", { style: "currency", currency: "MXN" })}
                sub={`activo ${Number(d.mxn_view.asset_component_mxn).toLocaleString("es-MX", { maximumFractionDigits: 0 })} · cambiario ${Number(d.mxn_view.fx_component_mxn).toLocaleString("es-MX", { maximumFractionDigits: 0 })}`} />
            )}
          </div>
          <div className="tablewrap">
            <table>
              <caption className="sr">Impacto por posición</caption>
              <thead><tr><th scope="col">Posición</th><th scope="col" className="num">Antes</th><th scope="col" className="num">Después</th><th scope="col" className="num">Cambio</th></tr></thead>
              <tbody>
                {d.positions.map((p) => (
                  <tr key={p.ticker}><th scope="row">{p.ticker}</th><td className="num">{usd(p.before_usd)}</td><td className="num">{usd(p.after_usd)}</td><td className="num">{pctRaw(p.change_pct)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <Notes items={d.assumptions} />
        </>
      )}
    </div>
  );
}

/* --------------------------------- Riesgo -------------------------------- */
function Risk() {
  const guard = useGuard();
  const [state, setState] = useState({ status: "loading" });
  const [win, setWin] = useState("365");

  const fetchRisk = useCallback(
    async (w) => {
      try {
        return { status: "ready", data: await api(`/api/v1/portfolio/risk?window_days=${encodeURIComponent(w)}`) };
      } catch (err) {
        return guard(err) ? null : { status: "error", message: err.message };
      }
    },
    [guard],
  );
  useEffect(() => {
    let alive = true;
    fetchRisk("365").then((r) => { if (alive && r) setState(r); });
    return () => { alive = false; };
  }, [fetchRisk]);

  function recalc(e) {
    e.preventDefault();
    setState({ status: "loading" });
    fetchRisk(win).then((r) => r && setState(r));
  }

  const d = state.status === "ready" ? state.data : null;
  const tickers = d?.correlation_matrix ? Object.keys(d.correlation_matrix) : [];
  return (
    <div className="stack">
      <form className="row-actions" onSubmit={recalc}>
        <label htmlFor="win">Ventana (días)</label>
        <input id="win" value={win} onChange={(e) => setWin(e.target.value)} inputMode="numeric" size={6} />
        <button type="submit" className="ghost">Recalcular</button>
      </form>
      {state.status === "loading" && <p role="status">Calculando…</p>}
      {state.status === "error" && <p role="alert" className="error">{state.message}</p>}
      {d && !d.evaluable && <div className="card empty"><h2>Sin historial</h2><p>{d.note ?? "Descarga el historial con refresh_price_history."}</p><Notes items={d.warnings} /></div>}
      {d?.evaluable && (
        <>
          <div className="stats">
            {d.portfolio && <Stat label="Volatilidad del portafolio" value={pct(d.portfolio.annualized_volatility)} sub={`anualizada · ${d.portfolio.observations} días · cobertura ${pct(d.portfolio.covered_share_of_invested, 0)}`} />}
            {d.portfolio && <Stat label="Beneficio de diversificar" value={pct(d.portfolio.diversification_benefit)} sub="vs. promedio ponderado de sus activos" />}
            <Stat label="Correlación promedio" value={d.average_pairwise_correlation == null ? "—" : Number(d.average_pairwise_correlation).toFixed(2)} sub={`ventana ${d.window.from} → ${d.window.to}`} />
          </div>
          <div className="tablewrap">
            <table>
              <caption className="sr">Riesgo por emisora</caption>
              <thead><tr><th scope="col">Emisora</th><th scope="col" className="num">Volatilidad anual</th><th scope="col" className="num">Caída máxima</th><th scope="col" className="num">Retorno ventana</th><th scope="col" className="num">Días</th></tr></thead>
              <tbody>
                {Object.entries(d.assets).map(([t, a]) => (
                  <tr key={t}><th scope="row">{t}</th><td className="num">{pct(a.annualized_volatility)}</td><td className="num">{pct(a.max_drawdown)}</td><td className="num">{pct(a.return_over_window)}</td><td className="num">{a.observations}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          {tickers.length > 1 && (
            <div className="tablewrap">
              <table>
                <caption className="sr">Matriz de correlaciones</caption>
                <thead><tr><th scope="col">Correl.</th>{tickers.map((t) => (<th key={t} scope="col" className="num">{t}</th>))}</tr></thead>
                <tbody>
                  {tickers.map((a) => (
                    <tr key={a}><th scope="row">{a}</th>{tickers.map((b) => (<td key={b} className="num">{d.correlation_matrix[a][b] == null ? "—" : Number(d.correlation_matrix[a][b]).toFixed(2)}</td>))}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Notes items={[...d.warnings, d.disclaimer]} />
        </>
      )}
    </div>
  );
}

/* ---------------------------------- Tesis --------------------------------- */
function Theses() {
  const guard = useGuard();
  const [list, setList] = useState(null);
  const [open, setOpen] = useState(null);
  const [msg, setMsg] = useState("");

  const fetchList = useCallback(async () => {
    try {
      return { list: await api("/api/v1/portfolio/theses") };
    } catch (err) {
      return guard(err) ? null : { error: err.message };
    }
  }, [guard]);
  const apply = (r) => {
    if (!r) return;
    if (r.list) setList(r.list);
    else setMsg(r.error);
  };
  useEffect(() => {
    let alive = true;
    fetchList().then((r) => { if (alive) apply(r); });
    return () => { alive = false; };
  }, [fetchList]);
  const reload = () => fetchList().then(apply);

  async function create(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const body = { title: f.get("title"), thesis: f.get("thesis"), risks: f.get("risks"), invalidation: f.get("invalidation"), horizon: f.get("horizon"), strategy: f.get("strategy") };
    if (num(f.get("instrument"))) body.instrument = num(f.get("instrument"));
    try {
      await api("/api/v1/portfolio/theses", { method: "POST", body });
      form.reset();
      setMsg("Tesis guardada (no se puede editar; agrega seguimientos).");
      reload();
    } catch (err) {
      if (!guard(err)) setMsg(err.message);
    }
  }

  async function show(id) {
    try {
      setOpen(await api(`/api/v1/portfolio/theses/${id}`));
    } catch (err) {
      if (!guard(err)) setMsg(err.message);
    }
  }

  async function follow(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    try {
      await api(`/api/v1/portfolio/theses/${open.id}/follow-ups`, { method: "POST", body: { status: f.get("status"), note: f.get("note") } });
      form.reset();
      show(open.id);
      reload();
    } catch (err) {
      if (!guard(err)) setMsg(err.message);
    }
  }

  return (
    <div className="stack">
      <form className="card form" onSubmit={create}>
        <fieldset>
          <legend>Nueva tesis (inmutable)</legend>
          <label className="field"><span>Título</span><input name="title" required /></label>
          <label className="field"><span>ID de instrumento (opcional)</span><input name="instrument" placeholder="guarda el precio de hoy" /></label>
          <label className="field"><span>Estrategia</span>
            <select name="strategy" defaultValue="long_term"><option value="long_term">Largo plazo</option><option value="swing">Swing</option><option value="day_trading">Day trading</option></select></label>
          <label className="field"><span>Horizonte</span><input name="horizon" placeholder="3 años" /></label>
          <label className="field"><span>Tesis</span><textarea name="thesis" required rows={3} /></label>
          <label className="field"><span>Riesgos</span><textarea name="risks" required rows={2} /></label>
          <label className="field"><span>Qué la invalidaría</span><textarea name="invalidation" required rows={2} /></label>
        </fieldset>
        <div className="row-actions"><button type="submit">Guardar tesis</button>{msg && <span role="status">{msg}</span>}</div>
      </form>

      {list && list.length === 0 && <div className="card empty"><h2>Sin tesis</h2><p>Escribe por qué compras y qué te haría cambiar de opinión.</p></div>}
      {list && list.length > 0 && (
        <div className="tablewrap">
          <table>
            <caption className="sr">Tesis</caption>
            <thead><tr><th scope="col">Título</th><th scope="col">Estado</th><th scope="col">Estrategia</th><th scope="col" className="num">Seguimientos</th><th scope="col"></th></tr></thead>
            <tbody>
              {list.map((t) => (
                <tr key={t.id}>
                  <th scope="row">{t.title}</th><td>{t.status}</td><td>{t.strategy}</td><td className="num">{t.follow_ups}</td>
                  <td><button type="button" className="ghost" onClick={() => show(t.id)}>Abrir</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {open && (
        <section className="card stack" aria-label="Detalle de la tesis">
          <h2>{open.title}</h2>
          <p><strong>Tesis:</strong> {open.thesis}</p>
          <p><strong>Riesgos:</strong> {open.risks}</p>
          <p><strong>La invalidaría:</strong> {open.invalidation}</p>
          <p className="muted">Precio al escribirla: {open.price ? `${Number(open.price).toFixed(2)} (${open.price_source.provider})` : "sin precio"}</p>
          <ul className="pending-list">
            {open.follow_ups.map((f) => (
              <li key={f.id}>[{f.status}] {f.note}{f.change_since_thesis != null ? ` — ${pct(f.change_since_thesis)} desde la tesis` : ""}</li>
            ))}
          </ul>
          <form className="row-actions" onSubmit={follow}>
            <select name="status" defaultValue="OPEN"><option value="OPEN">Abierta</option><option value="CONFIRMED">Confirmada</option><option value="INVALIDATED">Invalidada</option><option value="CLOSED">Cerrada</option></select>
            <input name="note" placeholder="Seguimiento" required size={40} />
            <button type="submit" className="ghost">Agregar seguimiento</button>
          </form>
        </section>
      )}
    </div>
  );
}

const PANELS = { exposure: Exposure, rebalance: Rebalance, shocks: Shocks, risk: Risk, theses: Theses };

export default function Inteligencia() {
  const [tab, setTab] = useState("exposure");
  const Panel = PANELS[tab];
  return (
    <main className="wrap">
      <header className="top">
        <div>
          <h1>Inteligencia de portafolio</h1>
          <p className="muted">Exposición, rebalanceo, shocks, riesgo histórico y diario de tesis. No es asesoría de inversión.</p>
        </div>
        <nav className="actions" aria-label="Principal"><Link className="btn ghost" href="/">Portafolio</Link></nav>
      </header>
      <div className="row-actions" role="tablist" aria-label="Secciones">
        {TABS.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "" : "ghost"} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>
      <Panel key={tab} />
    </main>
  );
}
