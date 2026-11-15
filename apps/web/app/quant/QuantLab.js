"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { createChart, LineSeries } from "lightweight-charts";
import { api, ApiError } from "../lib/api";

const pct = (v, d = 1) => (v == null ? "—" : `${(Number(v) * 100).toFixed(d)} %`);
const num = (v) => (typeof v !== "number" ? "—" : Number.isInteger(v) ? String(v) : v.toFixed(3));
const ic = (v) => (v == null ? "—" : `${Number(v) >= 0 ? "+" : ""}${Number(v).toFixed(3)}`);
const VERDICTS = {
  PROMOVER_A_SEGUIMIENTO: ["Merece seguimiento", "Cumple todos los criterios. No es una recomendación ni autoriza operar."],
  NO_PROMOVIDO: ["No promovido", "No cumple al menos un criterio."],
  DATOS_INSUFICIENTES: ["Datos insuficientes", "La muestra es demasiado chica para concluir algo."],
};

function palette() {
  const dark = typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
  return dark
    ? { bg: "#161f28", text: "#e6ebf0", grid: "#2a3541", a: "#6db0f0", b: "#f0d58a", c: "#cdb6f0" }
    : { bg: "#ffffff", text: "#15202b", grid: "#d9dee5", a: "#0f5fa8", b: "#6b4e00", c: "#51317f" };
}

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

/** Curvas de capital (base 100) de la estrategia y sus dos referencias. */
function EquityChart({ bt }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!ref.current || !bt?.dates) return undefined;
    const p = palette();
    const chart = createChart(ref.current, {
      height: 300,
      layout: { background: { color: p.bg }, textColor: p.text },
      grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
      rightPriceScale: { borderColor: p.grid },
      timeScale: { borderColor: p.grid },
    });
    const draw = (key, color, width) => {
      const eq = bt[key].equity;
      chart.addSeries(LineSeries, { color, lineWidth: width, priceLineVisible: false, title: "" }).setData(bt.dates.map((t, i) => ({ time: t, value: (eq[i] / bt.config.capital) * 100 })));
    };
    draw("benchmark_buy_hold", p.grid, 1);
    draw("benchmark_equal_weight", p.b, 2);
    draw("strategy", p.a, 2);
    chart.timeScale().fitContent();
    const resize = () => chart.applyOptions({ width: ref.current?.clientWidth ?? 600 });
    resize();
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); chart.remove(); };
  }, [bt]);
  return <div ref={ref} className="chartbox" role="img" aria-label="Curvas de capital de la estrategia y sus referencias" />;
}

function Detail({ id, onVerified }) {
  const guard = useGuard();
  const [state, setState] = useState({ status: "loading" });
  const [verify, setVerify] = useState(null);

  useEffect(() => {
    let alive = true;
    api(`/api/v1/quant/experiments/${id}`)
      .then((d) => { if (alive) setState({ status: "ready", data: d }); })
      .catch((err) => { if (alive && !guard(err)) setState({ status: "error", message: err.message }); });
    return () => { alive = false; };
  }, [id, guard]);

  if (state.status === "loading") return <p role="status">Cargando experimento…</p>;
  if (state.status === "error") return <p role="alert" className="error">{state.message}</p>;
  const d = state.data;
  const r = d.result;
  const [vLabel, vText] = VERDICTS[d.verdict] ?? [d.verdict, ""];
  const cand = r.models[r.candidate];
  const bt = cand.backtest?.strategy ? cand.backtest : null;
  const tick = (i) => r.meta.tickers?.[String(i)] ?? String(i);

  const runVerify = async () => {
    setVerify({ status: "loading" });
    try {
      setVerify({ status: "ready", data: await api(`/api/v1/quant/experiments/${id}/verify`, { method: "POST", body: {} }) });
      onVerified?.();
    } catch (err) {
      if (!guard(err)) setVerify({ status: "error", message: err.message });
    }
  };

  return (
    <div className="stack">
      <div className="card">
        <h2 style={{ margin: 0 }}>{d.name} <span className="badge">{vLabel}</span></h2>
        <p className="muted small">{vText} Candidato: <strong>{cand.label}</strong> (elegido solo con la validación).</p>
        <ul className="pending-list">{r.promotion.reasons.map((x) => (<li key={x}>{x}</li>))}</ul>
        <div className="tablewrap">
          <table>
            <thead><tr><th>Criterio</th><th>Valor</th><th>Umbral</th><th>Resultado</th></tr></thead>
            <tbody>
              {r.promotion.checks.map((c) => (
                <tr key={c.id}><td>{c.label}</td><td>{num(c.value)}</td><td>{c.direction} {num(c.threshold)}</td><td>{c.passed ? "Cumple" : "No cumple"}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">Base de retorno: {(r.meta.return_basis?.raw ?? 0) === 0 ? "dividendos reinvertidos (cierre ajustado)" : `${r.meta.return_basis?.raw} de ${r.meta.universe.length} emisoras sin ajuste por dividendos`}. Intento {d.trials} sobre estos mismos datos: el umbral de t se endurece con cada intento adicional (pruebas múltiples).</p>
      </div>

      <div className="card">
        <h2>Capacidad predictiva por modelo</h2>
        <p className="muted small">Rank IC: correlación de orden entre el puntaje y el retorno a {d.horizon} sesiones, por fecha. Cerca de 0 = sin señal. Los t usan muestra efectiva (corrigen el solape de etiquetas).</p>
        <div className="tablewrap">
          <table>
            <thead><tr><th>Modelo</th><th>Rank IC validación</th><th>Rank IC prueba</th><th>t ajustado</th><th>Fechas IC&gt;0</th><th>Neto de costos</th><th>vs universo igual</th></tr></thead>
            <tbody>
              {Object.entries(r.models).map(([k, m]) => (
                <tr key={k}>
                  <td>{m.label}{k === r.candidate ? " ★" : ""}</td>
                  {m.test ? (
                    <>
                      <td>{ic(m.valid.rank_ic.mean)}</td><td>{ic(m.test.rank_ic.mean)}</td>
                      <td>{m.test.rank_ic.t_adjusted == null ? "—" : m.test.rank_ic.t_adjusted.toFixed(2)}</td>
                      <td>{pct(m.test.rank_ic.pct_positive, 0)}</td>
                      <td>{m.backtest?.strategy ? pct(m.backtest.strategy.total_return) : "—"}</td>
                      <td>{m.backtest?.strategy ? pct(m.backtest.excess_vs_equal_weight) : "—"}</td>
                    </>
                  ) : (<td colSpan={6}>No disponible: {m.unavailable}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">
          {r.meta.split.mode === "walk_forward" ? `Walk-forward: ${(r.meta.split.folds ?? []).length} bloques de prueba consecutivos; en cada uno se entrena solo con lo anterior.` : "Una sola partición temporal."} Purga de {r.meta.split.gap_sessions} sesiones entre segmentos.
        </p>
        <div className="tablewrap">
          <table>
            <thead><tr><th>Bloque</th><th>Entrenamiento</th><th>Validación</th><th>Prueba</th><th>Rank IC del candidato</th></tr></thead>
            <tbody>
              {(r.meta.split.folds ?? []).map((f, i) => (
                <tr key={f.test[0]}>
                  <td>{i + 1}</td><td>{f.train[0]} → {f.train[1]} ({f.train[2]})</td><td>{f.valid[0]} → {f.valid[1]} ({f.valid[2]})</td><td>{f.test[0]} → {f.test[1]} ({f.test[2]})</td>
                  <td>{ic(cand.folds?.[i]?.rank_ic)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {bt && (
        <div className="card">
          <h2>Simulación del candidato en el periodo de prueba (neta de costos)</h2>
          <EquityChart bt={bt} />
          <p className="muted small">Azul: estrategia (top-{bt.config.top_k}, rebalanceo cada {bt.config.rebalance_days} sesiones). Amarillo: universo en partes iguales con el mismo rebalanceo y costos. Gris: compra y mantén. Base 100. Costos por lado: {bt.config.costs.commission_bps + bt.config.costs.half_spread_bps + bt.config.costs.slippage_bps} pb. Rotación anual {pct(bt.strategy.turnover_one_way_annual, 0)}; costos pagados {pct(bt.strategy.cost_drag_pct_capital, 2)} del capital; caída máxima {pct(bt.strategy.max_drawdown)}.</p>
          <ul className="pending-list">{bt.notes.map((n) => (<li key={n}>{n}</li>))}</ul>
        </div>
      )}

      {r.latest_scores.length > 0 && (
        <div className="card">
          <h2>Ranking más reciente del candidato ({r.meta.live_date})</h2>
          <p className="muted small">Orden de investigación, sin etiqueta de retorno aún. No es una señal de compra.</p>
          <div className="tablewrap">
            <table><thead><tr><th>#</th><th>Emisora</th><th>Puntaje</th></tr></thead>
              <tbody>{r.latest_scores.slice(0, 10).map((s, i) => (<tr key={s.instrument}><td>{i + 1}</td><td>{tick(s.instrument)}</td><td>{s.score.toFixed(4)}</td></tr>))}</tbody></table>
          </div>
        </div>
      )}

      <div className="card">
        <h2>Reproducibilidad</h2>
        <p className="small">Datos <code>{d.dataset_hash.slice(0, 16)}</code> · resultado <code>{d.result_hash.slice(0, 16)}</code> · versión {d.quant_version} · semilla {r.meta.seed} · {r.meta.rows} filas · {r.meta.universe.length} emisoras hasta {d.config.as_of}.</p>
        <div className="row-actions">
          <button type="button" onClick={runVerify} disabled={verify?.status === "loading"}>{verify?.status === "loading" ? "Verificando…" : "Volver a correr y comparar"}</button>
          {verify?.status === "ready" && <span role="status" className="badge">{verify.data.reproducible ? "Reproducible" : "No coincide"}</span>}
        </div>
        {verify?.status === "ready" && <p className="muted small">{verify.data.reason}</p>}
        {verify?.status === "error" && <p role="alert" className="error">{verify.message}</p>}
      </div>

      <div className="card">
        <h2>Limitaciones</h2>
        <ul className="pending-list">{r.meta.caveats.map((c) => (<li key={c}>{c}</li>))}</ul>
      </div>
    </div>
  );
}

export default function QuantLab() {
  const guard = useGuard();
  const [overview, setOverview] = useState(null);
  const [list, setList] = useState([]);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const [running, setRunning] = useState(false);
  const [horizon, setHorizon] = useState(20);
  const [models, setModels] = useState(null);
  const [seed, setSeed] = useState("0");
  const [topK, setTopK] = useState("3");
  const [folds, setFolds] = useState("4");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    Promise.all([api("/api/v1/quant/overview"), api("/api/v1/quant/experiments")])
      .then(([o, l]) => { if (alive) { setOverview(o); setList(l); } })
      .catch((err) => { if (alive && !guard(err)) setError(err.message); });
    return () => { alive = false; };
  }, [guard, reload]);

  const chosen = models ?? overview?.models.map((m) => m.id) ?? [];
  const toggle = (id) => setModels(chosen.includes(id) ? chosen.filter((m) => m !== id) : [...chosen, id]);

  const run = async (e) => {
    e.preventDefault();
    setRunning(true);
    setError(null);
    try {
      const d = await api("/api/v1/quant/experiments", { method: "POST", body: { horizon, models: chosen, seed: Number(seed), top_k: Number(topK), folds: Number(folds) } });
      setSelected(d.id);
      setReload((n) => n + 1);
    } catch (err) {
      if (!guard(err)) setError(err.message);
    } finally {
      setRunning(false);
    }
  };

  const small = overview && overview.universe.length < overview.criteria.min_universe;

  return (
    <main className="wrap">
      <header className="top">
        <div>
          <h1>Quant Lab</h1>
          <p className="muted">Investigación cuantitativa con evaluación fuera de muestra.</p>
        </div>
        <nav className="actions" aria-label="Principal"><Link className="btn ghost" href="/">Portafolio</Link></nav>
      </header>
      <p className="notice warn">{overview?.disclaimer ?? "Laboratorio de investigación: no es una recomendación de inversión."}</p>
      {error && <p role="alert" className="error">{error}</p>}

      {overview && (
        <section className="card" aria-label="Universo de datos">
          <h2>Datos disponibles</h2>
          <div className="stats">
            <div className="card stat"><span className="label">Emisoras con historial</span><strong>{overview.universe.length}</strong><span className="sub">mínimo {overview.criteria.min_universe} para poder concluir</span></div>
            <div className="card stat"><span className="label">Último cierre</span><strong>{overview.last_bar ?? "—"}</strong></div>
            <div className="card stat"><span className="label">LightGBM</span><strong>{overview.lightgbm_available ? "Disponible" : "No instalado"}</strong><span className="sub">entorno aislado en workers/qlib</span></div>
          </div>
          {small && <p className="notice warn">Con {overview.universe.length} emisoras el laboratorio solo puede decir «datos insuficientes»: pocas emisoras no permiten distinguir señal de ruido. Descarga historial largo de más emisoras (<code>refresh_price_history --range 5y</code>) antes de interpretar resultados.</p>}
        </section>
      )}

      <section className="card" aria-label="Nuevo experimento">
        <h2>Nuevo experimento</h2>
        <form onSubmit={run} className="stack">
          <div className="row-actions">
            <label className="field">Horizonte<select value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>{(overview?.horizons ?? [5, 20]).map((h) => (<option key={h} value={h}>{h} sesiones</option>))}</select></label>
            <label className="field">Semilla<input type="number" min="0" value={seed} onChange={(e) => setSeed(e.target.value)} /></label>
            <label className="field">Bloques walk-forward<input type="number" min="1" max={overview?.max_folds ?? 8} value={folds} onChange={(e) => setFolds(e.target.value)} /></label>
            <label className="field">Posiciones (top-K)<input type="number" min="1" max="20" value={topK} onChange={(e) => setTopK(e.target.value)} /></label>
          </div>
          <fieldset className="row-actions" style={{ border: 0, padding: 0 }}>
            <legend className="muted small">Modelos (hace falta al menos uno que aprenda: ridge o LightGBM)</legend>
            {(overview?.models ?? []).map((m) => (
              <label key={m.id} className="small"><input type="checkbox" checked={chosen.includes(m.id)} onChange={() => toggle(m.id)} /> {m.label}</label>
            ))}
          </fieldset>
          <div className="row-actions">
            <button type="submit" disabled={running || !overview}>{running ? "Entrenando y evaluando…" : "Correr experimento"}</button>
            <span className="muted small">Cada experimento queda guardado e inmutable; los intentos repetidos sobre los mismos datos endurecen el criterio.</span>
          </div>
        </form>
      </section>

      <section className="card" aria-label="Experimentos">
        <h2>Experimentos</h2>
        {list.length === 0 ? (<p className="muted">Aún no hay experimentos.</p>) : (
          <div className="tablewrap">
            <table>
              <thead><tr><th>#</th><th>Nombre</th><th>Horizonte</th><th>Candidato</th><th>Rank IC prueba</th><th>Veredicto</th><th /></tr></thead>
              <tbody>
                {list.map((e) => (
                  <tr key={e.id}>
                    <td>{e.id}</td><td>{e.name}</td><td>{e.horizon}</td><td>{e.candidate_label}</td><td>{ic(e.test_rank_ic)}</td>
                    <td>{(VERDICTS[e.verdict] ?? [e.verdict])[0]}</td>
                    <td><button type="button" className="ghost" onClick={() => setSelected(e.id)}>Ver</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selected && <Detail key={selected} id={selected} onVerified={() => setReload((n) => n + 1)} />}
    </main>
  );
}
