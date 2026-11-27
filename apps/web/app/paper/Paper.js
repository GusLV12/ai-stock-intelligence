"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { createChart, LineSeries } from "lightweight-charts";
import { api, ApiError } from "../lib/api";
import { chartPalette } from "../lib/chartTheme";

const money = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const pct = (v, d = 2) => (v == null ? "—" : `${(Number(v) * 100).toFixed(d)} %`);
const qty = (v) => (v == null ? "—" : String(v).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, ""));
const STATUS = { FILLED: "Ejecutada", REJECTED: "Rechazada", PENDING: "Pendiente", CANCELLED: "Cancelada", EXPIRED: "Vencida" };
const SIDE = { BUY: "Compra", SELL: "Venta" };

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

const palette = chartPalette;

function EquityChart({ curve }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!ref.current || !curve?.length) return undefined;
    const p = palette();
    const chart = createChart(ref.current, {
      height: 260,
      layout: { background: { color: p.bg }, textColor: p.text },
      grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
      rightPriceScale: { borderColor: p.grid },
      timeScale: { borderColor: p.grid },
    });
    chart.addSeries(LineSeries, { color: p.a, lineWidth: 2, priceLineVisible: false }).setData([...new Map(curve.map((c) => [c.date, { time: c.date, value: Number(c.equity) }])).values()]);
    chart.timeScale().fitContent();
    const resize = () => chart.applyOptions({ width: ref.current?.clientWidth ?? 600 });
    resize();
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); chart.remove(); };
  }, [curve]);
  return <div ref={ref} className="chartbox" role="img" aria-label="Curva de capital de la cuenta virtual" />;
}

function Account({ id }) {
  const guard = useGuard();
  const [data, setData] = useState({ status: "loading" });
  const [tick, setTick] = useState(0);
  const [form, setForm] = useState({ ticker: "", side: "BUY", quantity: "", order_type: "MARKET", limit_price: "", note: "" });
  const [msg, setMsg] = useState(null);
  const [exp, setExp] = useState("");
  const [proposal, setProposal] = useState(null);
  const [proposalSource, setProposalSource] = useState(null);
  const [journalText, setJournalText] = useState("");
  const [tab, setTab] = useState("positions");
  const [range, setRange] = useState("all");
  const [busy, setBusy] = useState(false);
  const reload = () => setTick((n) => n + 1);

  useEffect(() => {
    let alive = true;
    Promise.all([api(`/api/v1/paper/accounts/${id}`), api(`/api/v1/paper/accounts/${id}/orders`), api(`/api/v1/paper/accounts/${id}/equity`), api(`/api/v1/paper/accounts/${id}/journal`), api("/api/v1/quant/experiments")])
      .then(([summary, orders, equity, journal, experiments]) => { if (alive) setData({ status: "ready", summary, orders, equity, journal, experiments }); })
      .catch((err) => { if (alive && !guard(err)) setData({ status: "error", message: err.message }); });
    return () => { alive = false; };
  }, [id, tick, guard]);

  if (data.status === "loading") return <p role="status">Cargando cuenta…</p>;
  if (data.status === "error") return <p role="alert" className="error">{data.message}</p>;
  const { summary: s, orders, equity, journal, experiments } = data;
  const m = equity.metrics;

  const act = async (fn) => {
    setMsg(null);
    setBusy(true);
    try { await fn(); reload(); } catch (err) { if (!guard(err)) setMsg(err.message); } finally { setBusy(false); }
  };
  const updateOrder = (changes) => { setProposalSource(null); setForm((current) => ({ ...current, ...changes })); };
  const submit = (e) => {
    e.preventDefault();
    act(async () => {
      const o = await api(`/api/v1/paper/accounts/${id}/orders`, { method: "POST", body: { ...form, limit_price: form.limit_price || undefined, experiment: proposalSource ?? undefined } });
      setProposalSource(null);
      setMsg(o.status === "FILLED" ? `Ejecutada a ${money(o.fill_price)} (referencia ${money(o.reference_price)}).` : o.status === "REJECTED" ? `Rechazada: ${o.reason}` : "Orden pendiente.");
    });
  };
  const loadProposal = () => act(async () => { setProposal(await api(`/api/v1/paper/accounts/${id}/proposal?experiment=${exp}&top_k=3`)); });

  const currency = s.account.currency;
  const complete = s.valuation_complete !== false;
  const lastDate = equity.curve.at(-1)?.date;
  let start = null;
  if (lastDate && range !== "all") {
    const d = new Date(`${lastDate}T00:00:00Z`);
    if (range === "ytd") d.setUTCMonth(0, 1);
    else d.setUTCMonth(d.getUTCMonth() - Number(range));
    start = d.toISOString().slice(0, 10);
  }
  const visibleCurve = start ? equity.curve.filter((point) => point.date >= start) : equity.curve;
  const config = s.account.config || {};
  const tabItems = [["positions", `Posiciones (${s.positions.length})`], ["orders", `Órdenes (${orders.length})`], ["journal", `Diario (${journal.length})`]];

  return (
    <div className="paper-account">
      <div className="paper-stats">
        <div className="card paper-stat"><span>Capital virtual</span><strong>{complete ? `${money(s.equity)} ${currency}` : "No evaluable"}</strong><small>Inicial: {money(s.account.initial_cash)} {currency}</small></div>
        <div className="card paper-stat"><span>Rendimiento</span><strong className={Number(s.return_pct) >= 0 ? "pos" : "neg"}>{complete ? `${Number(s.return_pct).toFixed(2)} %` : "No evaluable"}</strong><small>Simulado, neto de comisiones</small></div>
        <div className="card paper-stat"><span>Efectivo virtual</span><strong>{money(s.cash)} {currency}</strong><small>Disponible en la cuenta</small></div>
        <div className="card paper-stat"><span>Utilidad realizada</span><strong className={Number(s.realized) >= 0 ? "pos" : "neg"}>{money(s.realized)} {currency}</strong><small>Comisiones: {money(s.fees)} {currency}</small></div>
      </div>
      <p className="notice warn">{s.notice}</p>
      {!complete && <p className="notice warn">Hay posiciones sin precio de referencia. El capital y rendimiento no son evaluables; la curva histórica puede contener estimaciones a costo.</p>}
      <div className="paper-workspace">
        <div className="paper-main">
          <section className="card paper-chart"><div className="paper-heading"><div><h2>Evolución del capital virtual</h2><p className="muted small">Simulación en {currency}, reconstruida con ejecuciones simuladas y cierres diarios guardados; los datos pueden tener retraso.</p></div><div className="paper-ranges" aria-label="Rango de la gráfica">{[["1", "1M"], ["3", "3M"], ["6", "6M"], ["ytd", "YTD"], ["12", "1A"], ["all", "Todo"]].map(([v, label]) => <button type="button" key={v} aria-pressed={range === v} className={range === v ? "" : "ghost"} onClick={() => setRange(v)}>{label}</button>)}</div></div>
            {visibleCurve.length > 1 ? <EquityChart curve={visibleCurve} /> : <p className="paper-empty">{visibleCurve.length ? "Aún no hay suficientes fechas para mostrar una curva." : "No hay datos para el rango seleccionado."}</p>}
            <p className="muted small">Rango calculado desde la última fecha disponible ({lastDate || "sin datos"}). {m.days} observaciones · caída máxima {pct(m.max_drawdown)} · operaciones cerradas {m.closed_trades} · acierto {pct(m.win_rate, 0)} · deslizamiento adverso medio {m.avg_adverse_slippage_bps == null ? "—" : `${Number(m.avg_adverse_slippage_bps).toFixed(1)} pb`}. {m.sharpe_note || ""} {m.sample_note || ""}</p>
          </section>
          <section className="card paper-records">
            <nav className="paper-tabs" aria-label="Actividad virtual">{tabItems.map(([key, label]) => <button key={key} type="button" className={tab === key ? "" : "ghost"} aria-pressed={tab === key} onClick={() => setTab(key)}>{label}</button>)}</nav>
            {tab === "positions" && <div><h2>Posiciones</h2>{s.positions.length === 0 ? <p className="muted">Sin posiciones.</p> : <div className="tablewrap"><table><thead><tr><th>Emisora</th><th>Cantidad</th><th>Costo medio</th><th>Precio ({currency})</th><th>Valor ({currency})</th><th>No realizada</th><th>Fuente y antigüedad</th></tr></thead><tbody>{s.positions.map((p) => <tr key={p.instrument}><td>{p.ticker}</td><td>{qty(p.quantity)}</td><td>{money(p.avg_cost)}</td><td>{money(p.price)}</td><td>{money(p.market_value)}</td><td className={p.unrealized == null ? "" : Number(p.unrealized) >= 0 ? "pos" : "neg"}>{money(p.unrealized)}</td><td>{p.price_source ? `${p.price_source.provider} · ${p.price_source.observed_at} · ${Number(p.price_source.age_days).toFixed(1)} d` : "Sin precio"}</td></tr>)}</tbody></table></div>}</div>}
            {tab === "orders" && <div><div className="paper-heading"><h2>Órdenes simuladas</h2><button type="button" className="ghost" disabled={busy} onClick={() => act(async () => { const r = await api(`/api/v1/paper/accounts/${id}/process`, { method: "POST", body: {} }); setMsg(`Procesadas: ${r.filled.length} ejecutadas, ${r.rejected.length} rechazadas, ${r.expired.length} vencidas, ${r.still_pending.length} pendientes.`); })}>Reprocesar pendientes</button></div>{orders.length === 0 ? <p className="muted">Aún no hay órdenes simuladas.</p> : <div className="tablewrap"><table><thead><tr><th>#</th><th>Emisora</th><th>Lado</th><th>Cant.</th><th>Tipo</th><th>Estado</th><th>Referencia</th><th>Ejecución</th><th>Fuente</th><th>Desliz. pb</th><th>Motivo</th><th /></tr></thead><tbody>{orders.map((o) => <tr key={o.id}><td>{o.id}</td><td>{o.ticker}</td><td>{SIDE[o.side]}</td><td>{qty(o.quantity)}</td><td>{o.order_type === "LIMIT" ? `Límite ${money(o.limit_price)}` : "Mercado"}</td><td>{STATUS[o.status]}</td><td>{money(o.reference_price)}</td><td>{money(o.fill_price)}</td><td>{o.reference_source?.provider ? `${o.reference_source.provider} · ${o.reference_source.observed_at || ""}` : "—"}</td><td>{o.adverse_slippage_bps == null ? "—" : Number(o.adverse_slippage_bps).toFixed(1)}</td><td>{o.reason || o.note}</td><td>{o.status === "PENDING" && <button type="button" className="ghost" disabled={busy} onClick={() => act(() => api(`/api/v1/paper/accounts/${id}/orders/${o.id}/cancel`, { method: "POST", body: {} }))}>Cancelar</button>}</td></tr>)}</tbody></table></div>}</div>}
            {tab === "journal" && <div><h2>Diario de decisiones</h2><form className="stack" onSubmit={(e) => { e.preventDefault(); act(async () => { await api(`/api/v1/paper/accounts/${id}/journal`, { method: "POST", body: { kind: "DECISION", text: journalText } }); setJournalText(""); }); }}><label className="field">Qué piensas y qué esperas (queda inmutable)<textarea rows={3} maxLength={3000} value={journalText} onChange={(e) => setJournalText(e.target.value)} required /></label><div><button type="submit" disabled={busy}>Guardar en el diario</button></div></form>{journal.length ? <ul className="pending-list">{journal.map((j) => <li key={j.id}><strong>{String(j.created_at).slice(0, 16).replace("T", " ")}</strong> · {j.text}</li>)}</ul> : <p className="muted">Aún no hay decisiones registradas.</p>}</div>}
          </section>
          <section className="card paper-proposal"><h2>Propuesta desde un experimento del Quant Lab</h2><p className="muted small">Consulta un experimento existente y decide si quieres cargar una propuesta al formulario. No se ejecuta sola.</p><div className="row-actions"><label className="field">Experimento<select value={exp} onChange={(e) => { setExp(e.target.value); setProposal(null); setProposalSource(null); }}><option value="">—</option>{experiments.map((x) => <option key={x.id} value={x.id}>#{x.id} {x.name} ({x.verdict})</option>)}</select></label><button type="button" disabled={!exp || busy} onClick={loadProposal}>Ver propuesta</button></div>{proposal && <><ul className="pending-list">{proposal.warnings.map((w) => <li key={w}>{w}</li>)}</ul>{proposal.proposals.length ? <div className="tablewrap"><table><thead><tr><th>Emisora</th><th>Lado</th><th>Cantidad</th><th>Motivo</th><th /></tr></thead><tbody>{proposal.proposals.map((p) => <tr key={`${p.ticker}${p.side}`}><td>{p.ticker}</td><td>{SIDE[p.side]}</td><td>{qty(p.quantity)}</td><td>{p.reason}</td><td><button type="button" className="ghost" onClick={() => { setForm({ ...form, ticker: p.ticker, side: p.side, quantity: qty(p.quantity), order_type: "MARKET" }); setProposalSource(Number(exp)); }}>Cargar en el formulario</button></td></tr>)}</tbody></table></div> : <p className="muted">No hay propuestas disponibles.</p>}</>}</section>
        </div>
        <aside className="paper-aside">
          <section className="card paper-order"><h2>Nueva orden simulada</h2><p className="muted small">Solo modifica esta cuenta virtual.</p><form onSubmit={submit} className="paper-order-form"><label className="field">Emisora<input value={form.ticker} onChange={(e) => updateOrder({ ticker: e.target.value })} required placeholder="AAPL" /></label><label className="field">Lado<select value={form.side} onChange={(e) => updateOrder({ side: e.target.value })}><option value="BUY">Compra</option><option value="SELL">Venta</option></select></label><label className="field">Cantidad<input value={form.quantity} onChange={(e) => updateOrder({ quantity: e.target.value })} required inputMode="decimal" /></label><label className="field">Tipo<select value={form.order_type} onChange={(e) => updateOrder({ order_type: e.target.value })}><option value="MARKET">A mercado</option><option value="LIMIT">Limitada</option></select></label>{form.order_type === "LIMIT" && <label className="field">Precio límite ({currency})<input value={form.limit_price} onChange={(e) => updateOrder({ limit_price: e.target.value })} inputMode="decimal" required /></label>}<label className="field">Nota (por qué)<input value={form.note} maxLength={300} onChange={(e) => setForm({ ...form, note: e.target.value })} /></label><button type="submit" disabled={busy}>{busy ? "Procesando…" : "Simular orden"}</button>{proposalSource && <p className="muted small">Propuesta cargada del experimento #{proposalSource}. Cambiar los datos de la orden elimina esta asociación.</p>}</form><p className="muted small">Último precio almacenado; no es tiempo real. Deslizamiento: {config.slippage_bps ?? 5} pb · comisión: {config.commission_bps ?? 0} pb (mínimo {money(config.commission_min ?? 0)} {currency}) · antigüedad máxima: {config.max_price_age_days ?? 5} días.</p>{msg && <p role="status" className="notice">{msg}</p>}</section>
          <section className="card paper-guide"><h2>Guía rápida</h2><ul><li>Usa capital virtual; no hay conexión con brokers.</li><li>Una orden a mercado puede ejecutarse o rechazarse al crearla.</li><li>Una orden límite puede ejecutarse al crearla o quedar pendiente. Revísala y reprocesa desde Órdenes.</li><li>Los precios tienen fuente y antigüedad; pueden estar desactualizados.</li><li>Solo posiciones largas: no se permiten ventas en corto ni apalancamiento.</li></ul><details><summary>Ver guía completa</summary><p>Primero crea una cuenta y revisa su efectivo. Introduce una emisora del catálogo, cantidad y tipo; una venta no puede superar tus títulos. El límite puede vencer a los 30 días y sus ejecuciones con cierres diarios son aproximadas.</p><p>El diario y las órdenes decididas no se pueden borrar ni editar. Las propuestas de Quant Lab son orientativas y no se ejecutan automáticamente. Los resultados de práctica no prueban una ventaja futura.</p></details></section>
        </aside>
      </div>
    </div>
  );
}

export default function Paper() {
  const guard = useGuard();
  const [list, setList] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const [name, setName] = useState("Práctica");
  const [cash, setCash] = useState("10000");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    api("/api/v1/paper/accounts")
      .then((d) => { if (alive) { setList(d); setSelected((current) => current ?? d.accounts[0]?.id ?? null); } })
      .catch((err) => { if (alive && !guard(err)) setError(err.message); });
    return () => { alive = false; };
  }, [guard, tick]);

  const create = async (e) => {
    e.preventDefault();
    setError(null);
    try {
      const a = await api("/api/v1/paper/accounts", { method: "POST", body: { name, initial_cash: cash } });
      setSelected(a.account.id);
      setTick((n) => n + 1);
    } catch (err) {
      if (!guard(err)) setError(err.message);
    }
  };

  return (
    <main className="wrap paper-page">
      <header className="top">
        <div><p className="paper-eyebrow">LABORATORIO DE PRÁCTICA</p><h1>Paper trading</h1><p className="muted">Practica con una cuenta virtual y revisa tus decisiones.</p></div><span className="paper-badge">SIMULACIÓN · SIN DINERO REAL</span>
        <nav className="actions" aria-label="Principal"><Link className="btn ghost" href="/">Portafolio</Link><Link className="btn ghost" href="/quant">Quant Lab</Link></nav>
      </header>
      <p className="notice warn">{list?.disclaimer ?? "Cuenta virtual de práctica: no se envía ninguna orden real."}</p>
      {error && <p role="alert" className="error">{error}</p>}
      <section className="card paper-accounts" aria-label="Cuentas">
        <div className="paper-heading"><div><h2>Cuentas virtuales</h2><p className="muted small">Selecciona una cuenta o crea una nueva.</p></div>{list && list.accounts.length > 0 && <label className="field">Cuenta virtual<select value={selected ?? ""} onChange={(e) => setSelected(Number(e.target.value))}>{list.accounts.map((a) => <option key={a.id} value={a.id}>{a.name} · {a.currency}</option>)}</select></label>}</div>
        {!list && !error && <p role="status">Cargando cuentas…</p>}
        {list && list.accounts.length === 0 && <p className="muted">Aún no tienes una cuenta virtual.</p>}
        <details open={!!list && list.accounts.length === 0}><summary>Crear cuenta virtual</summary>
        <form onSubmit={create} className="row-actions">
          <label className="field">Nombre<input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} required /></label>
          <label className="field">Capital inicial (USD)<input value={cash} onChange={(e) => setCash(e.target.value)} inputMode="decimal" required /></label>
          <button type="submit">Crear cuenta virtual</button>
        </form>
        </details>
      </section>
      {selected && <Account key={selected} id={selected} />}
    </main>
  );
}
