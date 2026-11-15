"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { createChart, LineSeries } from "lightweight-charts";
import { api, ApiError } from "../lib/api";

const money = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const pct = (v, d = 2) => (v == null ? "—" : `${(Number(v) * 100).toFixed(d)} %`);
const qty = (v) => (v == null ? "—" : String(Number(v)));
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

function palette() {
  const dark = typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
  return dark ? { bg: "#161f28", text: "#e6ebf0", grid: "#2a3541", a: "#6db0f0" } : { bg: "#ffffff", text: "#15202b", grid: "#d9dee5", a: "#0f5fa8" };
}

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
  const [journalText, setJournalText] = useState("");
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
    try { await fn(); reload(); } catch (err) { if (!guard(err)) setMsg(err.message); }
  };
  const submit = (e) => {
    e.preventDefault();
    act(async () => {
      const o = await api(`/api/v1/paper/accounts/${id}/orders`, { method: "POST", body: { ...form, limit_price: form.limit_price || undefined, experiment: exp ? Number(exp) : undefined } });
      setMsg(o.status === "FILLED" ? `Ejecutada a ${money(o.fill_price)} (referencia ${money(o.reference_price)}).` : o.status === "REJECTED" ? `Rechazada: ${o.reason}` : "Orden pendiente.");
    });
  };
  const loadProposal = () => act(async () => { setProposal(await api(`/api/v1/paper/accounts/${id}/proposal?experiment=${exp}&top_k=3`)); });

  return (
    <div className="stack">
      <div className="stats">
        <div className="card stat"><span className="label">Capital</span><strong>{money(s.equity)}</strong><span className="sub">{s.account.currency} · inicial {money(s.account.initial_cash)}</span></div>
        <div className="card stat"><span className="label">Rendimiento</span><strong>{Number(s.return_pct).toFixed(2)} %</strong><span className="sub">simulado, neto de comisiones</span></div>
        <div className="card stat"><span className="label">Efectivo</span><strong>{money(s.cash)}</strong></div>
        <div className="card stat"><span className="label">Utilidad realizada</span><strong>{money(s.realized)}</strong><span className="sub">no realizada {money(s.unrealized)} · comisiones {money(s.fees)}</span></div>
      </div>
      <p className="notice warn">{s.notice}</p>

      <div className="card">
        <h2>Curva de capital</h2>
        <EquityChart curve={equity.curve} />
        <p className="muted small">
          {m.days} días · caída máxima {pct(m.max_drawdown)} · operaciones cerradas {m.closed_trades} · acierto {pct(m.win_rate, 0)} · deslizamiento adverso medio {m.avg_adverse_slippage_bps == null ? "—" : `${Number(m.avg_adverse_slippage_bps).toFixed(1)} pb`}.
          {m.sharpe_note ? ` ${m.sharpe_note}` : ""} {m.sample_note ?? ""}
        </p>
      </div>

      <div className="card">
        <h2>Nueva orden simulada</h2>
        <form onSubmit={submit} className="stack">
          <div className="row-actions">
            <label className="field">Emisora<input value={form.ticker} onChange={(e) => setForm({ ...form, ticker: e.target.value })} required placeholder="AAPL" /></label>
            <label className="field">Lado<select value={form.side} onChange={(e) => setForm({ ...form, side: e.target.value })}><option value="BUY">Compra</option><option value="SELL">Venta</option></select></label>
            <label className="field">Cantidad<input value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} required inputMode="decimal" /></label>
            <label className="field">Tipo<select value={form.order_type} onChange={(e) => setForm({ ...form, order_type: e.target.value })}><option value="MARKET">A mercado</option><option value="LIMIT">Limitada</option></select></label>
            {form.order_type === "LIMIT" && <label className="field">Precio límite<input value={form.limit_price} onChange={(e) => setForm({ ...form, limit_price: e.target.value })} inputMode="decimal" /></label>}
          </div>
          <label className="field">Nota (por qué)<input value={form.note} maxLength={300} onChange={(e) => setForm({ ...form, note: e.target.value })} /></label>
          <div className="row-actions">
            <button type="submit">Simular orden</button>
            <button type="button" className="ghost" onClick={() => act(async () => { const r = await api(`/api/v1/paper/accounts/${id}/process`, { method: "POST", body: {} }); setMsg(`Procesadas: ${r.filled.length} ejecutadas, ${r.expired.length} vencidas, ${r.still_pending.length} pendientes.`); })}>Reprocesar pendientes</button>
          </div>
          {msg && <p role="status" className="notice">{msg}</p>}
        </form>
        <p className="muted small">La ejecución usa el último precio conocido con su antigüedad, más deslizamiento y comisión de la cuenta. No existe ninguna conexión con un broker.</p>
      </div>

      <div className="card">
        <h2>Propuesta desde un experimento del Quant Lab</h2>
        <div className="row-actions">
          <label className="field">Experimento<select value={exp} onChange={(e) => { setExp(e.target.value); setProposal(null); }}><option value="">—</option>{experiments.map((x) => (<option key={x.id} value={x.id}>#{x.id} {x.name} ({x.verdict})</option>))}</select></label>
          <button type="button" disabled={!exp} onClick={loadProposal}>Ver propuesta</button>
        </div>
        {proposal && (
          <>
            <ul className="pending-list">{proposal.warnings.map((w) => (<li key={w}>{w}</li>))}</ul>
            <div className="tablewrap"><table><thead><tr><th>Emisora</th><th>Lado</th><th>Cantidad</th><th>Motivo</th><th /></tr></thead>
              <tbody>{proposal.proposals.map((p) => (<tr key={`${p.ticker}${p.side}`}><td>{p.ticker}</td><td>{SIDE[p.side]}</td><td>{qty(p.quantity)}</td><td>{p.reason}</td>
                <td><button type="button" className="ghost" onClick={() => setForm({ ...form, ticker: p.ticker, side: p.side, quantity: String(Number(p.quantity)), order_type: "MARKET" })}>Cargar en el formulario</button></td></tr>))}</tbody></table></div>
          </>
        )}
      </div>

      <div className="card">
        <h2>Posiciones</h2>
        {s.positions.length === 0 ? <p className="muted">Sin posiciones.</p> : (
          <div className="tablewrap"><table><thead><tr><th>Emisora</th><th>Cantidad</th><th>Costo medio</th><th>Precio</th><th>Valor</th><th>No realizada</th><th>Precio de</th></tr></thead>
            <tbody>{s.positions.map((p) => (<tr key={p.instrument}><td>{p.ticker}</td><td>{qty(p.quantity)}</td><td>{money(p.avg_cost)}</td><td>{money(p.price)}</td><td>{money(p.market_value)}</td><td>{money(p.unrealized)}</td>
              <td>{p.price_source ? `${p.price_source.provider}, hace ${Number(p.price_source.age_days).toFixed(1)} d` : "—"}</td></tr>))}</tbody></table></div>
        )}
      </div>

      <div className="card">
        <h2>Órdenes</h2>
        <div className="tablewrap"><table><thead><tr><th>#</th><th>Emisora</th><th>Lado</th><th>Cant.</th><th>Tipo</th><th>Estado</th><th>Referencia</th><th>Ejecución</th><th>Desliz. pb</th><th>Motivo</th><th /></tr></thead>
          <tbody>{orders.map((o) => (<tr key={o.id}><td>{o.id}</td><td>{o.ticker}</td><td>{SIDE[o.side]}</td><td>{qty(o.quantity)}</td><td>{o.order_type === "LIMIT" ? `Límite ${money(o.limit_price)}` : "Mercado"}</td>
            <td>{STATUS[o.status]}</td><td>{money(o.reference_price)}</td><td>{money(o.fill_price)}</td><td>{o.adverse_slippage_bps == null ? "—" : Number(o.adverse_slippage_bps).toFixed(1)}</td><td>{o.reason || o.note}</td>
            <td>{o.status === "PENDING" && <button type="button" className="ghost" onClick={() => act(() => api(`/api/v1/paper/accounts/${id}/orders/${o.id}/cancel`, { method: "POST", body: {} }))}>Cancelar</button>}</td></tr>))}</tbody></table></div>
      </div>

      <div className="card">
        <h2>Diario de decisiones</h2>
        <form className="stack" onSubmit={(e) => { e.preventDefault(); act(async () => { await api(`/api/v1/paper/accounts/${id}/journal`, { method: "POST", body: { kind: "DECISION", text: journalText } }); setJournalText(""); }); }}>
          <label className="field">Qué piensas y qué esperas (queda inmutable)<textarea rows={3} maxLength={3000} value={journalText} onChange={(e) => setJournalText(e.target.value)} required /></label>
          <div><button type="submit">Guardar en el diario</button></div>
        </form>
        <ul className="pending-list">{journal.map((j) => (<li key={j.id}><strong>{String(j.created_at).slice(0, 16).replace("T", " ")}</strong> · {j.text}</li>))}</ul>
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
      .then((d) => { if (alive) setList(d); })
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
    <main className="wrap">
      <header className="top">
        <div><h1>Paper trading</h1><p className="muted">Cuenta virtual de práctica: sin dinero real y sin broker.</p></div>
        <nav className="actions" aria-label="Principal"><Link className="btn ghost" href="/">Portafolio</Link><Link className="btn ghost" href="/quant">Quant Lab</Link></nav>
      </header>
      <p className="notice warn">{list?.disclaimer ?? "Cuenta virtual de práctica: no se envía ninguna orden real."}</p>
      {error && <p role="alert" className="error">{error}</p>}
      <section className="card" aria-label="Cuentas">
        <h2>Cuentas</h2>
        {list && list.accounts.length > 0 && (
          <div className="tablewrap"><table><thead><tr><th>Nombre</th><th>Capital</th><th>Rendimiento</th><th /></tr></thead>
            <tbody>{list.accounts.map((a) => (<tr key={a.id}><td>{a.name}</td><td>{money(a.equity)} {a.currency}</td><td>{Number(a.return_pct).toFixed(2)} %</td><td><button type="button" className="ghost" onClick={() => setSelected(a.id)}>Abrir</button></td></tr>))}</tbody></table></div>
        )}
        <form onSubmit={create} className="row-actions">
          <label className="field">Nombre<input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} required /></label>
          <label className="field">Capital inicial (USD)<input value={cash} onChange={(e) => setCash(e.target.value)} inputMode="decimal" required /></label>
          <button type="submit">Crear cuenta virtual</button>
        </form>
      </section>
      {selected && <Account key={selected} id={selected} />}
    </main>
  );
}
