"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { CandlestickSeries, createChart, HistogramSeries, LineSeries } from "lightweight-charts";
import { api, ApiError } from "../lib/api";

const TABS = [["daily", "Diario"], ["intraday", "Intradía"], ["plan", "Plan de operación"], ["alerts", "Alertas"], ["liquidity", "Liquidez"]];
const num = (v) => (String(v ?? "").trim() === "" ? undefined : String(v).trim().replace(",", "."));
const money = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 4 }));
const SIGNAL_IDS = ["breakout", "reclaim_ma", "oversold", "oversold_bounce", "trend_up", "macd_bull_cross", "volume_spike", "near_level"];

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
  return dark
    ? { bg: "#161f28", text: "#e6ebf0", grid: "#2a3541", up: "#8fd6a5", down: "#f2a79d", a: "#6db0f0", b: "#f0d58a", c: "#cdb6f0" }
    : { bg: "#ffffff", text: "#15202b", grid: "#d9dee5", up: "#1d6a35", down: "#9a2a1c", a: "#0f5fa8", b: "#6b4e00", c: "#51317f" };
}

/** Gráfica de precio con paneles opcionales (RSI y MACD). Se recrea cuando cambian los datos. */
function PriceChart({ data, overlays, panes, height = 420 }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!ref.current || !data) return undefined;
    const p = palette();
    const chart = createChart(ref.current, {
      height,
      layout: { background: { color: p.bg }, textColor: p.text, panes: { separatorColor: p.grid } },
      grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
      rightPriceScale: { borderColor: p.grid },
      timeScale: { borderColor: p.grid, timeVisible: !!data.intraday },
    });
    if (data.candles?.length && data.candles[0].open != null) {
      const candles = chart.addSeries(CandlestickSeries, { upColor: p.up, downColor: p.down, borderVisible: false, wickUpColor: p.up, wickDownColor: p.down });
      candles.setData(data.candles.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
    } else if (data.candles?.length) {
      chart.addSeries(LineSeries, { color: p.a, lineWidth: 2 }).setData(data.candles.map((c) => ({ time: c.time, value: c.close })));
    }
    const line = (series, color, opts = {}, pane = 0) => {
      if (series?.length) chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, ...opts }, pane).setData(series);
    };
    if (overlays.sma) line(data.series?.sma, p.b);
    if (overlays.ema) { line(data.series?.ema_fast, p.a); line(data.series?.ema_slow, p.c); }
    if (overlays.bollinger) { line(data.series?.bollinger_upper, p.grid, { lineStyle: 2 }); line(data.series?.bollinger_lower, p.grid, { lineStyle: 2 }); }
    if (data.vwap) line(data.vwap, p.b, { lineWidth: 2 });
    (data.levelLines ?? []).forEach((lv) => {
      const first = data.candles[0]?.time;
      const last = data.candles[data.candles.length - 1]?.time;
      if (first && last) chart.addSeries(LineSeries, { color: lv.color, lineWidth: 1, lineStyle: 1, priceLineVisible: false, lastValueVisible: false }, 0)
        .setData([{ time: first, value: lv.price }, { time: last, value: lv.price }]);
    });
    let pane = 1;
    if (panes.rsi && data.series?.rsi?.length) {
      line(data.series.rsi, p.a, { lineWidth: 2, priceLineVisible: false }, pane);
      [30, 70].forEach((v) => line(data.series.rsi.map((x) => ({ time: x.time, value: v })), p.grid, { lineStyle: 2 }, pane));
      pane += 1;
    }
    if (panes.macd && data.series?.macd?.length) {
      chart.addSeries(HistogramSeries, { color: p.c, priceLineVisible: false, lastValueVisible: false }, pane)
        .setData(data.series.macd_histogram.map((x) => ({ time: x.time, value: x.value, color: x.value >= 0 ? p.up : p.down })));
      line(data.series.macd, p.a, {}, pane);
      line(data.series.macd_signal, p.b, {}, pane);
    }
    chart.timeScale().fitContent();
    const resize = () => chart.applyOptions({ width: ref.current?.clientWidth ?? 600 });
    resize();
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); chart.remove(); };
  }, [data, overlays, panes, height]);
  return <div ref={ref} className="chartbox" role="img" aria-label="Gráfica de precios con indicadores" />;
}

function Notes({ items }) {
  const list = (items ?? []).filter(Boolean);
  return list.length ? <div className="card"><h2>Avisos</h2><ul className="pending-list">{list.map((w) => (<li key={w}>{w}</li>))}</ul></div> : null;
}

/* --------------------------------- Diario --------------------------------- */
function Daily({ ticker, symbols }) {
  const guard = useGuard();
  const [state, setState] = useState({ status: "loading" });
  const [overlays, setOverlays] = useState({ sma: false, ema: true, bollinger: false });
  const [panes, setPanes] = useState({ rsi: true, macd: false });

  useEffect(() => {
    if (!ticker) return undefined;
    let alive = true;
    api(`/api/v1/technical/analyze?ticker=${encodeURIComponent(ticker)}&days=300`)
      .then((d) => { if (alive) setState({ status: "ready", data: d }); })
      .catch((err) => { if (alive && !guard(err)) setState({ status: "error", message: err.message }); });
    return () => { alive = false; };
  }, [ticker, guard]);

  if (!ticker) return <div className="card empty"><h2>Sin emisoras</h2><p>{symbols === null ? "Cargando…" : "Descarga el historial con refresh_price_history."}</p></div>;
  if (state.status === "loading") return <p role="status">Cargando…</p>;
  if (state.status === "error") return <p role="alert" className="error">{state.message}</p>;
  const d = state.data;
  if (!d.evaluable) return <div className="card empty"><h2>Sin historial de {ticker}</h2><p>{d.note}</p></div>;
  const f = d.freshness;
  const chartData = { ...d, levelLines: (d.context.levels ?? []).slice(0, 4).map((lv) => ({ price: lv.price, color: lv.kind === "support" ? "#1d6a35" : "#9a2a1c" })) };
  return (
    <div className="stack">
      <p className={`notice ${f.stale ? "warn" : ""}`} role="status">
        {f.provider} · última vela {f.last_bar} (hace {f.age_days} d){f.stale ? " — desactualizada" : ""}. {f.note}
        {!d.has_ohlc && " Sin máximos/mínimos/volumen guardados: se dibuja una línea y faltan ATR y volumen relativo (vuelve a ejecutar refresh_price_history)."}
      </p>
      <div className="row-actions">
        {[["sma", "SMA 20"], ["ema", "EMA 50/200"], ["bollinger", "Bollinger"]].map(([k, l]) => (
          <label key={k} className="check"><input type="checkbox" checked={overlays[k]} onChange={(e) => setOverlays({ ...overlays, [k]: e.target.checked })} /> {l}</label>
        ))}
        {[["rsi", "RSI"], ["macd", "MACD"]].map(([k, l]) => (
          <label key={k} className="check"><input type="checkbox" checked={panes[k]} onChange={(e) => setPanes({ ...panes, [k]: e.target.checked })} /> {l}</label>
        ))}
      </div>
      <PriceChart data={chartData} overlays={overlays} panes={panes} height={panes.rsi || panes.macd ? 560 : 420} />
      <div className="stats">
        <div className="card stat"><span className="label">Último cierre</span><strong>{money(d.context.last_close)}</strong><span className="sub">{String(d.as_of)}</span></div>
        <div className="card stat"><span className="label">RSI 14</span><strong>{d.context.rsi == null ? "—" : Number(d.context.rsi).toFixed(1)}</strong></div>
        <div className="card stat"><span className="label">ATR 14</span><strong>{d.context.atr == null ? "—" : money(d.context.atr)}</strong></div>
      </div>
      <h2>Señales (eventos reproducibles, no recomendaciones)</h2>
      <div className="grid2">
        {d.signals.map((s) => (
          <article className="card" key={s.id}>
            <h3 style={{ margin: 0 }}>{s.name} <span className="badge" title={s.reason ?? ""}>{s.fired === null ? "No evaluable" : s.fired ? "Activa" : "No activa"}</span></h3>
            <p className="muted small">{s.rule}</p>
            {s.reason && <p className="muted small">{s.reason}</p>}
            {s.conditions.map((c) => (<p key={c.text} className="small">{c.text}: {JSON.stringify(c.value)}</p>))}
          </article>
        ))}
      </div>
      {d.plan_suggestion && (
        <div className="card"><h2>Aritmética de ejemplo</h2>
          <p>Entrada {money(d.plan_suggestion.entry)} · stop {money(d.plan_suggestion.stop)} · objetivo {money(d.plan_suggestion.target)}</p>
          <p className="muted small">{d.plan_suggestion.basis}</p></div>
      )}
      <Notes items={[d.note]} />
      <p className="muted small">Gráficas: <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView Lightweight Charts™</a> (Apache-2.0).</p>
    </div>
  );
}

/* --------------------------------- Intradía -------------------------------- */
function Intraday({ ticker }) {
  const guard = useGuard();
  const [state, setState] = useState({ status: "idle" });

  async function load() {
    setState({ status: "loading" });
    try {
      setState({ status: "ready", data: await api(`/api/v1/technical/intraday?ticker=${encodeURIComponent(ticker)}`) });
    } catch (err) {
      if (!guard(err)) setState({ status: "error", message: err.message });
    }
  }
  const d = state.status === "ready" ? state.data : null;
  const chartData = d?.evaluable
    ? {
        candles: d.bars.map((b) => ({ time: Math.floor(new Date(b.time).getTime() / 1000), open: b.open, high: b.high, low: b.low, close: b.close })),
        vwap: d.vwap.map((p) => ({ time: Math.floor(new Date(p.time).getTime() / 1000), value: Number(p.value) })),
        intraday: true,
        levelLines: d.orb?.range_high ? [{ price: d.orb.range_high, color: "#1d6a35" }, { price: d.orb.range_low, color: "#9a2a1c" }] : [],
      }
    : null;
  return (
    <div className="stack">
      <div className="row-actions">
        <button type="button" onClick={load} disabled={!ticker || state.status === "loading"}>{state.status === "loading" ? "Consultando…" : `Cargar sesión de ${ticker ?? "…"}`}</button>
        <span className="muted small">Consulta a Yahoo Finance (no oficial) solo el símbolo; máx. 6 por minuto.</span>
      </div>
      {state.status === "error" && <p role="alert" className="error">{state.message}</p>}
      {d && !d.evaluable && <div className="card empty"><h2>Sin velas útiles</h2><p>{d.note}</p></div>}
      {d?.evaluable && (
        <>
          <p className={`notice ${d.freshness.state === "retrasado" || !d.usable ? "warn" : ""}`} role="status">
            Sesión {d.session} · {d.freshness.state.replace("_", " ")} (última vela hace {d.freshness.age_minutes} min). {d.freshness.note}
            {!d.usable && ` Faltan franjas: ${d.missing_slots.join(", ")} — sesión NO usable.`}
          </p>
          <PriceChart data={chartData} overlays={{}} panes={{}} height={420} />
          <div className="stats">
            <div className="card stat"><span className="label">Rango de apertura ({d.orb.or_minutes ?? 15} min)</span><strong>{money(d.orb.range_low)} – {money(d.orb.range_high)}</strong></div>
            <div className="card stat"><span className="label">Ruptura alcista</span><strong>{d.orb.hit ? `${d.orb.hit.time} a ${money(d.orb.hit.close)}` : "No"}</strong></div>
            <div className="card stat"><span className="label">Ruptura bajista</span><strong>{d.orb.breakdown ? `${d.orb.breakdown.time} a ${money(d.orb.breakdown.close)}` : "No"}</strong></div>
            <div className="card stat"><span className="label">VWAP final</span><strong>{d.vwap.length ? money(d.vwap[d.vwap.length - 1].value) : "—"}</strong></div>
          </div>
          <p className="muted small">{d.orb.rule}</p>
          <Notes items={[d.note, `Higiene: ${Object.entries(d.hygiene).filter(([, v]) => v).map(([k, v]) => `${k} ${v}`).join(", ") || "sin descartes"}.`]} />
        </>
      )}
    </div>
  );
}

/* ---------------------------------- Plan ---------------------------------- */
function Plan() {
  const guard = useGuard();
  const [state, setState] = useState({ status: "idle" });
  async function run(e) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body = { entry: num(f.get("entry")), stop: num(f.get("stop")), target: num(f.get("target")), capital: num(f.get("capital")) };
    ["risk_budget", "slippage_pct", "commission_per_trade", "daily_loss_so_far"].forEach((k) => { if (num(f.get(k))) body[k] = num(f.get(k)); });
    if (num(f.get("shares_scale"))) body.shares_scale = num(f.get("shares_scale"));
    const limits = {};
    ["max_risk_per_trade_pct", "max_position_pct", "min_reward_risk", "max_daily_loss_pct"].forEach((k) => { if (num(f.get(k))) limits[k] = num(f.get(k)); });
    if (Object.keys(limits).length) body.limits = limits;
    setState({ status: "loading" });
    try {
      setState({ status: "ready", data: await api("/api/v1/technical/trade-plan", { method: "POST", body }) });
    } catch (err) {
      if (!guard(err)) setState({ status: "error", message: err.message });
    }
  }
  const d = state.status === "ready" ? state.data : null;
  return (
    <div className="stack">
      <form className="card form" onSubmit={run}>
        <fieldset>
          <legend>Operación (solo compras)</legend>
          {[["entry", "Entrada", "100"], ["stop", "Stop", "98"], ["target", "Objetivo", "104"], ["capital", "Capital", "10000"],
            ["risk_budget", "Riesgo a asumir (opcional)", "50"], ["shares_scale", "Decimales en títulos", "0"],
            ["slippage_pct", "Deslizamiento (%)", "0"], ["commission_per_trade", "Comisión por operación", "0"], ["daily_loss_so_far", "Pérdida de hoy", "0"]].map(([n, l, ph]) => (
            <label className="field" key={n}><span>{l}</span><input name={n} placeholder={ph} /></label>
          ))}
        </fieldset>
        <fieldset>
          <legend>Tus límites (opcionales)</legend>
          {[["max_risk_per_trade_pct", "Riesgo máx. por operación (%)", "1"], ["max_position_pct", "Posición máx. (%)", "25"], ["min_reward_risk", "R:R mínima", "1.5"], ["max_daily_loss_pct", "Pérdida diaria máx. (%)", "2"]].map(([n, l, ph]) => (
            <label className="field" key={n}><span>{l}</span><input name={n} placeholder={ph} /></label>
          ))}
        </fieldset>
        <button type="submit" disabled={state.status === "loading"}>Evaluar</button>
      </form>
      {state.status === "error" && <p role="alert" className="error">{state.message}</p>}
      {d && (
        <>
          <p className={`notice ${d.verdict === "BLOCK" ? "warn" : ""}`} role="status"><strong>{d.verdict === "PASS" ? "APROBADA" : "BLOQUEADA"}</strong> por tus límites. Es aritmética, no una orden.</p>
          <div className="stats">
            <div className="card stat"><span className="label">Títulos</span><strong>{Number(d.shares)}</strong><span className="sub">limitado por {d.limited_by}</span></div>
            <div className="card stat"><span className="label">Valor de la posición</span><strong>{money(d.position_value)}</strong></div>
            <div className="card stat"><span className="label">Riesgo total</span><strong>{money(d.risk_total)}</strong></div>
            <div className="card stat"><span className="label">R:R bruta / neta</span><strong>{Number(d.reward_risk_gross).toFixed(2)} / {d.reward_risk_net == null ? "—" : Number(d.reward_risk_net).toFixed(2)}</strong></div>
          </div>
          <div className="card"><h2>Límites</h2>
            <ul className="pending-list">{d.checks.map((c) => (<li key={c.limit}>{c.passed ? "✔" : "✘"} {c.detail}</li>))}</ul></div>
          <Notes items={d.warnings} />
        </>
      )}
    </div>
  );
}

/* --------------------------------- Alertas -------------------------------- */
function Alerts({ symbols }) {
  const guard = useGuard();
  const [list, setList] = useState(null);
  const [msg, setMsg] = useState("");
  const [kind, setKind] = useState("PRICE_ABOVE");

  const fetchList = useCallback(async () => {
    try {
      return { list: await api("/api/v1/technical/alerts") };
    } catch (err) {
      return guard(err) ? null : { error: err.message };
    }
  }, [guard]);
  const apply = (r) => { if (!r) return; if (r.list) setList(r.list); else setMsg(r.error); };
  useEffect(() => {
    let alive = true;
    fetchList().then((r) => { if (alive) apply(r); });
    return () => { alive = false; };
  }, [fetchList]);
  const reload = () => fetchList().then(apply);

  async function create(e) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body = { ticker: f.get("ticker"), kind, note: f.get("note") };
    if (kind === "SIGNAL") body.signal_id = f.get("signal_id"); else body.threshold = num(f.get("threshold"));
    try {
      await api("/api/v1/technical/alerts", { method: "POST", body });
      setMsg("Alerta creada.");
      reload();
    } catch (err) {
      if (!guard(err)) setMsg(err.message);
    }
  }
  async function evaluate() {
    try {
      const r = await api("/api/v1/technical/alerts/evaluate", { method: "POST", body: {} });
      setMsg(r.triggered.length ? `${r.triggered.length} alerta(s) disparada(s).` : "Ninguna se ha cumplido con los datos guardados.");
      reload();
    } catch (err) {
      if (!guard(err)) setMsg(err.message);
    }
  }
  async function cancel(id) {
    try { await api(`/api/v1/technical/alerts/${id}/cancel`, { method: "POST", body: {} }); reload(); } catch (err) { if (!guard(err)) setMsg(err.message); }
  }

  return (
    <div className="stack">
      <form className="card form" onSubmit={create}>
        <fieldset>
          <legend>Nueva alerta (un solo disparo; no envía mensajes)</legend>
          <label className="field"><span>Emisora</span>
            <select name="ticker">{(symbols ?? []).map((s) => (<option key={s.ticker} value={s.ticker}>{s.ticker}</option>))}</select></label>
          <label className="field"><span>Tipo</span>
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="PRICE_ABOVE">Precio sobre</option><option value="PRICE_BELOW">Precio bajo</option><option value="SIGNAL">Señal técnica</option></select></label>
          {kind === "SIGNAL"
            ? <label className="field"><span>Señal</span><select name="signal_id">{SIGNAL_IDS.map((s) => (<option key={s} value={s}>{s}</option>))}</select></label>
            : <label className="field"><span>Umbral</span><input name="threshold" placeholder="150" required /></label>}
          <label className="field"><span>Nota</span><input name="note" /></label>
        </fieldset>
        <div className="row-actions"><button type="submit">Crear alerta</button><button type="button" className="ghost" onClick={evaluate}>Evaluar ahora</button>{msg && <span role="status">{msg}</span>}</div>
      </form>
      {list && list.length === 0 && <div className="card empty"><h2>Sin alertas</h2></div>}
      {list && list.length > 0 && (
        <div className="tablewrap">
          <table>
            <caption className="sr">Alertas</caption>
            <thead><tr><th scope="col">Emisora</th><th scope="col">Condición</th><th scope="col">Estado</th><th scope="col">Detalle</th><th scope="col"></th></tr></thead>
            <tbody>
              {list.map((a) => (
                <tr key={a.id}>
                  <th scope="row">{a.ticker}</th>
                  <td>{a.kind === "SIGNAL" ? `Señal ${a.signal_id}` : `${a.kind === "PRICE_ABOVE" ? "Precio ≥" : "Precio ≤"} ${money(a.threshold)}`}{a.note ? ` — ${a.note}` : ""}</td>
                  <td>{a.triggered_at ? "Disparada" : a.active ? "Activa" : "Cancelada"}</td>
                  <td className="small">{a.triggered_at ? (a.triggered_detail.price ? `${a.triggered_detail.price} (${a.triggered_detail.provider}, hace ${a.triggered_detail.age_hours} h${a.triggered_detail.stale ? ", dato viejo" : ""})` : a.triggered_detail.rule) : ""}</td>
                  <td>{a.active && <button type="button" className="ghost" onClick={() => cancel(a.id)}>Cancelar</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* -------------------------------- Liquidez -------------------------------- */
const TIER_LABEL = { alta: "alta", media: "media", baja: "baja", "muy baja": "muy baja" };
const fmtInt = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 }));
const fmtPct = (v) => (v == null ? "—" : `${(Number(v) * 100).toLocaleString("en-US", { maximumFractionDigits: 3 })} %`);

function SpreadCell({ s }) {
  if (!s || s.bps == null) return <span className="muted">no evaluable</span>;
  return s.resolvable ? <>{Number(s.bps).toFixed(1)} pb <span className="muted">(±{Number(s.se_bps).toFixed(1)})</span></> : <span className="muted" title={s.note}>≈ 0 (no distinguible)</span>;
}

function Liquidity({ ticker }) {
  const guard = useGuard();
  const [port, setPort] = useState({ status: "loading" });
  const [qty, setQty] = useState("100");
  const [one, setOne] = useState({ status: "idle" });

  useEffect(() => {
    let alive = true;
    api("/api/v1/technical/liquidity/portfolio")
      .then((data) => alive && setPort({ status: "ready", data }))
      .catch((err) => { if (alive && !guard(err)) setPort({ status: "error", message: err.message }); });
    return () => { alive = false; };
  }, [guard]);

  async function check(e) {
    e.preventDefault();
    setOne({ status: "loading" });
    try {
      const q = num(qty);
      setOne({ status: "ready", data: await api(`/api/v1/technical/liquidity?ticker=${encodeURIComponent(ticker)}${q ? `&quantity=${encodeURIComponent(q)}` : ""}`) });
    } catch (err) {
      if (!guard(err)) setOne({ status: "error", message: err.message });
    }
  }

  const d = port.data;
  const o = one.data;
  return (
    <>
      <div className="card">
        <h2>Liquidez de tu cartera</h2>
        <p className="muted">Tamaño de cada posición frente al volumen diario negociado (ventana de 20 días). Estimación con cierres diarios: no hay libro de órdenes.</p>
        {port.status === "loading" && <p>Cargando…</p>}
        {port.status === "error" && <p role="alert" className="error">{port.message}</p>}
        {d && (
          <>
            <p>{d.summary.positions} posiciones · {d.summary.evaluable} evaluables · {d.summary.size_high} con tamaño alto · {d.summary.size_visible} con tamaño visible{d.summary.not_evaluable.length ? ` · sin datos: ${d.summary.not_evaluable.join(", ")}` : ""}</p>
            <div className="table-wrap">
              <table>
                <thead><tr><th scope="col">Emisora</th><th scope="col">Liquidez</th><th scope="col" className="num">Volumen diario (USD)</th><th scope="col" className="num">Tu posición / volumen</th><th scope="col" className="num">Días para operarla</th><th scope="col">Spread estimado</th></tr></thead>
                <tbody>
                  {d.rows.map((r) => (
                    <tr key={r.ticker}>
                      <th scope="row">{r.ticker}</th>
                      <td>{r.evaluable ? TIER_LABEL[r.tier] : <span className="muted" title={r.reason}>sin datos</span>}</td>
                      <td className="num">{r.evaluable ? fmtInt(r.dollar_adv) : "—"}</td>
                      <td className="num">{r.evaluable ? fmtPct(r.position?.share_of_adv) : "—"}</td>
                      <td className="num">{r.evaluable ? Number(r.position?.days_to_trade_at_participation ?? 0).toFixed(2) : "—"}</td>
                      <td>{r.evaluable ? <SpreadCell s={r.spread_estimate} /> : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted">«Días para operarla» supone consumir como máximo {Math.round(d.participation_assumed * 100)} % del volumen diario. {d.disclaimer}</p>
          </>
        )}
      </div>

      <form className="card" onSubmit={check} aria-label="Consulta de orden hipotética">
        <h2>¿Y si operara N títulos de {ticker || "—"}?</h2>
        <div className="row-actions">
          <label htmlFor="liq-qty">Títulos</label>
          <input id="liq-qty" value={qty} onChange={(e) => setQty(e.target.value)} inputMode="decimal" />
          <button type="submit" disabled={!ticker || one.status === "loading"}>Calcular</button>
        </div>
        {one.status === "error" && <p role="alert" className="error">{one.message}</p>}
        {o && !o.evaluable && <p>{o.reason}</p>}
        {o && o.evaluable && (
          <ul className="pending-list">
            <li>Volumen diario promedio: {fmtInt(o.adv_shares)} títulos ({fmtInt(o.dollar_adv)} USD) · liquidez {o.tier}.</li>
            {o.position && <li>Esa cantidad es {fmtPct(o.position.share_of_adv)} del volumen diario (tamaño {o.position.size_flag}); tardarías ≈ {o.position.days_to_trade_at_participation} días con {Math.round(o.position.participation_assumed * 100)} % de participación.</li>}
            {o.position?.impact_sqrt_bps != null && <li>Impacto aproximado (raíz cuadrada, coeficiente 1): ≈ {o.position.impact_sqrt_bps} pb. Es un orden de magnitud, no una predicción de costo.</li>}
            <li>Spread: <SpreadCell s={o.spread_estimate} /> en {o.spread_estimate.window_days} días.</li>
            <li className="muted">{o.caveats.join(" ")}</li>
          </ul>
        )}
      </form>
    </>
  );
}

/* ---------------------------------- Raíz ---------------------------------- */
export default function Terminal() {
  const guard = useGuard();
  const [tab, setTab] = useState("daily");
  const [symbols, setSymbols] = useState(null);
  const [ticker, setTicker] = useState("");

  useEffect(() => {
    let alive = true;
    api("/api/v1/technical/symbols")
      .then((s) => {
        if (!alive) return;
        setSymbols(s);
        setTicker((cur) => cur || (s.find((x) => x.bars > 0) ?? s[0])?.ticker || "");
      })
      .catch((err) => { if (alive) guard(err); });
    return () => { alive = false; };
  }, [guard]);

  return (
    <main className="wrap">
      <header className="top">
        <div>
          <h1>Terminal técnica</h1>
          <p className="muted">Indicadores y señales sobre cierres diarios (fuente gratuita, no tiempo real). No es asesoría de inversión.</p>
        </div>
        <nav className="actions" aria-label="Principal">
          <Link className="btn ghost" href="/inteligencia">Inteligencia</Link>
          <Link className="btn ghost" href="/">Portafolio</Link>
        </nav>
      </header>
      <div className="row-actions">
        <label htmlFor="sym">Emisora</label>
        <select id="sym" value={ticker} onChange={(e) => setTicker(e.target.value)}>
          {(symbols ?? []).map((s) => (<option key={s.ticker} value={s.ticker}>{s.ticker}{s.held ? " ●" : ""}{s.bars ? ` (${s.bars} d)` : " (sin historial)"}</option>))}
        </select>
        <div role="tablist" aria-label="Secciones" className="row-actions">
          {TABS.map(([k, l]) => (<button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "" : "ghost"} onClick={() => setTab(k)}>{l}</button>))}
        </div>
      </div>
      {tab === "daily" && <Daily key={ticker} ticker={ticker} symbols={symbols} />}
      {tab === "intraday" && <Intraday key={ticker} ticker={ticker} />}
      {tab === "plan" && <Plan />}
      {tab === "alerts" && <Alerts symbols={symbols} />}
      {tab === "liquidity" && <Liquidity key={ticker} ticker={ticker} />}
    </main>
  );
}
