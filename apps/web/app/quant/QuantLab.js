"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { createChart, LineSeries } from "lightweight-charts";
import { api, ApiError } from "../lib/api";
import { chartPalette } from "../lib/chartTheme";
import { MotionPanel } from "../lib/Motion";

const pct = (v, d = 1) => (v == null ? "—" : `${(Number(v) * 100).toFixed(d)} %`);
const num = (v) => (typeof v !== "number" ? "—" : Number.isInteger(v) ? String(v) : v.toFixed(3));
const ic = (v) => (v == null ? "—" : `${Number(v) >= 0 ? "+" : ""}${Number(v).toFixed(3)}`);
const VERDICTS = {
  PROMOVER_A_SEGUIMIENTO: ["Merece seguimiento", "Cumple todos los criterios. No es una recomendación ni autoriza operar."],
  NO_PROMOVIDO: ["No promovido", "No cumple al menos un criterio."],
  DATOS_INSUFICIENTES: ["Datos insuficientes", "La muestra es demasiado chica para concluir algo."],
};

const palette = chartPalette;
const MODEL_HELP = {
  momentum_20: "Ordena por rendimiento de las últimas 20 sesiones.",
  momentum_60: "Ordena por rendimiento de las últimas 60 sesiones.",
  reversal_5: "Compara el comportamiento reciente en 5 sesiones.",
  low_vol: "Favorece menor volatilidad histórica.",
  random: "Control aleatorio para comparar la señal.",
  ridge: "Regresión lineal con regularización.",
  lightgbm: "Modelo de árboles que aprende de los factores.",
  qlib_lgbm: "Usa Alpha158 y LightGBM en Qlib; tarda varios minutos.",
};

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
    draw("benchmark_buy_hold", p.c, 1);
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
        <details className="quant-checks"><summary>Ver criterios y motivos del veredicto</summary>
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
        </details>
      </div>

      <div className="quant-flow" aria-label="Flujo de evaluación">
        <div><strong>1 · Entrenamiento</strong><span>Aprende con datos anteriores.</span></div>
        <div><strong>2 · Validación</strong><span>Selecciona el candidato.</span></div>
        <div><strong>3 · Prueba</strong><span>Evalúa fuera de muestra.</span></div>
      </div>

      {bt ? (
        <div className="card quant-chart-card">
          <h2>Simulación fuera de muestra · base 100</h2>
          <p className="muted small">Periodo de prueba, neto de costos. No representa una inversión real.</p>
          <EquityChart bt={bt} />
          <div className="quant-legend"><span><i className="quant-dot strategy" /> Estrategia</span><span><i className="quant-dot equal" /> Universo igual</span><span><i className="quant-dot hold" /> Compra y mantén</span></div>
          <p className="muted small">Top-{bt.config.top_k}, rebalanceo cada {bt.config.rebalance_days} sesiones. Costos por lado: {bt.config.costs.commission_bps + bt.config.costs.half_spread_bps + bt.config.costs.slippage_bps} pb. Rotación anual {pct(bt.strategy.turnover_one_way_annual, 0)}; costos pagados {pct(bt.strategy.cost_drag_pct_capital, 2)} del capital; caída máxima {pct(bt.strategy.max_drawdown)}.</p>
          <ul className="pending-list">{bt.notes.map((n) => (<li key={n}>{n}</li>))}</ul>
        </div>
      ) : <div className="card quant-empty"><h2>Simulación fuera de muestra</h2><p className="muted">No hay una simulación disponible para este experimento.</p></div>}

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
      .then(([o, l]) => { if (alive) { setOverview(o); setList(l); setSelected((current) => current ?? l[0]?.id ?? null); } })
      .catch((err) => { if (alive && !guard(err)) setError(err.message); });
    return () => { alive = false; };
  }, [guard, reload]);

  const chosen = models ?? overview?.models.filter((m) => m.default).map((m) => m.id) ?? [];
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
    <main className="wrap quant-page">
      <header className="top quant-heading">
        <div><p className="quant-eyebrow">INVESTIGACIÓN CUANTITATIVA</p><h1>Quant Lab</h1><p className="muted">Diseña, evalúa y compara experimentos históricos fuera de muestra.</p></div>
        <nav className="actions" aria-label="Accesos de Quant Lab"><a className="quant-guide-link" href="#guia-quant">Guía de uso</a><Link className="btn ghost" href="/">Portafolio</Link></nav>
      </header>
      <p className="notice warn">{overview?.disclaimer ?? "Laboratorio de investigación: no es una recomendación de inversión."}</p>
      {error && <p role="alert" className="error">{error}</p>}
      {!overview && !error && <p role="status">Cargando datos del laboratorio…</p>}
      {overview && <section className="quant-overview" aria-label="Universo de datos">
        <h2 className="sr">Datos disponibles</h2>
        <div className="quant-stat"><span>Emisoras con historial</span><strong>{overview.universe.length}</strong><small>Mínimo {overview.criteria.min_universe} para evaluar</small></div>
        <div className="quant-stat"><span>Último cierre disponible</span><strong>{overview.last_bar ?? "—"}</strong><small>Fecha más reciente del universo</small></div>
        <div className="quant-stat"><span>LightGBM</span><strong>{overview.lightgbm_available ? "Disponible" : "No instalado"}</strong><small>Modelo de árboles</small></div>
        <div className="quant-stat"><span>Qlib</span><strong>{overview.qlib_available ? "Disponible" : "No instalado"}</strong><small>Modelo opcional</small></div>
      </section>}
      {small && <p className="notice warn">Con {overview.universe.length} emisoras el laboratorio solo puede decir «datos insuficientes»: se necesitan al menos {overview.criteria.min_universe} emisoras para evaluar.</p>}
      <div className="quant-workspace">
        <section className="card quant-config" aria-label="Nuevo experimento">
          <div className="quant-panel-title"><span className="quant-symbol" aria-hidden="true">⚗</span><div><h2>Nuevo experimento</h2><p className="muted small">Define el horizonte, la evaluación y los modelos.</p></div></div>
          <form onSubmit={run} className="quant-form">
            <label className="quant-field"><span><strong>Horizonte</strong><small>Sesiones para medir el retorno posterior.</small></span><select value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>{(overview?.horizons ?? [5, 20]).map((h) => (<option key={h} value={h}>{h} sesiones</option>))}</select></label>
            <label className="quant-field"><span><strong>Bloques walk-forward</strong><small>Periodos consecutivos de evaluación.</small></span><input type="number" min="1" max={overview?.max_folds ?? 8} value={folds} onChange={(e) => setFolds(e.target.value)} /></label>
            <label className="quant-field"><span><strong>Posiciones (top-K)</strong><small>Emisoras seleccionadas al rebalancear.</small></span><input type="number" min="1" max="20" value={topK} onChange={(e) => setTopK(e.target.value)} /></label>
            <label className="quant-field"><span><strong>Semilla</strong><small>Controla componentes aleatorios.</small></span><input type="number" min="0" value={seed} onChange={(e) => setSeed(e.target.value)} /></label>
            <fieldset className="quant-models"><legend>Modelos a comparar</legend><p className="muted small">Incluye al menos un modelo que aprenda: ridge, LightGBM o Qlib.</p>
              {(overview?.models ?? []).map((m) => <label key={m.id} className="quant-model"><input type="checkbox" checked={chosen.includes(m.id)} onChange={() => toggle(m.id)} /><span><strong>{m.label}</strong><small>{MODEL_HELP[m.id] ?? "Modelo de investigación."}{m.heavy && !overview.qlib_available ? " No instalado." : ""}</small></span></label>)}
            </fieldset>
            <button type="submit" disabled={running || !overview}>{running ? "Entrenando y evaluando…" : "Correr experimento"}</button>
            <p className="muted small">Cada intento se guarda y no se puede modificar. Repetirlo con los mismos datos endurece el criterio.</p>
          </form>
        </section>
        <section className="quant-results" aria-label="Resultado del experimento">
          <div className="quant-section-heading"><div><p className="quant-eyebrow">ANÁLISIS HISTÓRICO</p><h2>Resultado del experimento</h2></div><span className="muted small">Fuera de muestra</span></div>
          {selected ? <MotionPanel key={selected}><Detail id={selected} onVerified={() => setReload((n) => n + 1)} /></MotionPanel> : <div className="card quant-empty"><h2>Sin experimento seleccionado</h2><p className="muted">Al ejecutar o seleccionar un experimento podrás revisar sus métricas, la simulación y su reproducibilidad.</p><p className="muted small">No se muestran resultados de ejemplo.</p></div>}
        </section>
        <aside className="quant-aside">
          <section id="guia-quant" className="card quant-guide"><div className="quant-panel-title"><span className="quant-symbol" aria-hidden="true">ⓘ</span><div><h2>Guía rápida</h2><p className="muted small">Entiende cada paso antes de comenzar.</p></div></div>
            <dl><dt>Horizonte</dt><dd>Sesiones posteriores sobre las que se mide el retorno.</dd><dt>Walk-forward</dt><dd>Entrena y valida con datos anteriores a cada bloque de prueba.</dd><dt>Top-K</dt><dd>Número de emisoras elegidas en cada rebalanceo simulado.</dd><dt>Rank IC</dt><dd>Relación entre el orden de los puntajes y el de los retornos posteriores. Cerca de cero indica poca señal.</dd></dl>
            <details className="quant-guide-more"><summary>Ver guía completa</summary><ol><li><strong>Revisa los datos.</strong> Comprueba cuántas emisoras hay y la fecha del último cierre; los datos pueden tener retraso.</li><li><strong>Configura el experimento.</strong> Elige horizonte, bloques, posiciones y modelos; Qlib es opcional y tarda más.</li><li><strong>Compara con referencias.</strong> El candidato se elige en validación; la prueba y la simulación muestran resultados históricos fuera de muestra, con costos.</li><li><strong>Lee el veredicto.</strong> Abre sus criterios y limitaciones: un resultado favorable solo significa que merece seguimiento.</li><li><strong>Verifica reproducibilidad.</strong> Repite y compara las huellas para identificar si los datos o el resultado cambiaron.</li></ol><p>Las señales y rankings son de investigación, no instrucciones de compra. El rendimiento pasado no garantiza resultados futuros.</p></details>
          </section>
          <section className="card quant-experiments" aria-label="Experimentos"><h2>Experimentos</h2><p className="muted small">Selecciona uno para ver su resultado.</p>
            {list.length === 0 ? <p className="muted">Aún no hay experimentos.</p> : <ul>{list.map((e) => <li key={e.id}><button type="button" className={selected === e.id ? "quant-experiment active" : "quant-experiment"} aria-pressed={selected === e.id} onClick={() => setSelected(e.id)}><span><strong>{e.name}</strong><small>{e.horizon} sesiones · {e.candidate_label}</small></span><span><strong>{(VERDICTS[e.verdict] ?? [e.verdict])[0]}</strong><small>Rank IC {ic(e.test_rank_ic)}</small></span></button></li>)}</ul>}
          </section>
        </aside>
      </div>
    </main>
  );
}
