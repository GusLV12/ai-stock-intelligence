"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { motion } from "motion/react";

const initial = { universe: "portfolio", currency: "USD", min_price: "", max_price: "", min_volume: "", rsi_min: "", rsi_max: "", ema: "", max_age: "" };

export default function Explorer({ symbols, onSelect, refreshVersion }) {
  const [filters, setFilters] = useState(initial);
  const [state, setState] = useState({ status: "loading" });
  const filtersRef = useRef(null);
  const resultsRef = useRef(null);
  const currencies = [...new Set(["USD", ...(symbols ?? []).map((s) => s.currency).filter(Boolean)])].sort();
  useEffect(() => {
    if (window.matchMedia("(max-width: 700px)").matches) {
      if (filtersRef.current) filtersRef.current.open = false;
      if (resultsRef.current) resultsRef.current.open = false;
    }
    let alive = true;
    const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== ""));
    api(`/api/v1/technical/explore?${query}`)
      .then((data) => { if (alive) setState({ status: "ready", data }); })
      .catch((error) => { if (alive) setState({ status: "error", message: error.message }); });
    return () => { alive = false; };
  // Se conservan los filtros elegidos al terminar la actualización.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshVersion]);

  function update(event) { setFilters({ ...filters, [event.target.name]: event.target.value }); }
  async function apply(event) {
    event.preventDefault();
    const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== ""));
    setState({ status: "loading" });
    try { setState({ status: "ready", data: await api(`/api/v1/technical/explore?${query}`) }); }
    catch (error) { setState({ status: "error", message: error.message }); }
  }
  function clear() { setFilters(initial); setState({ status: "idle" }); }
  const result = state.data;
  return (
    <>
      <details ref={filtersRef} className="technical-filters" open>
        <summary>Filtros técnicos</summary>
        <form className="technical-filter-form" onSubmit={apply}>
          <label>Universo<select name="universe" value={filters.universe} onChange={update}><option value="portfolio">Cartera</option><option value="research">Investigación</option></select></label>
          <label>Moneda<select name="currency" value={filters.currency} onChange={update}>{currencies.map((c) => <option key={c}>{c}</option>)}</select></label>
          <div className="technical-filter-pair"><label>Precio mínimo<input name="min_price" type="number" min="0" step="any" value={filters.min_price} onChange={update} /></label><label>Precio máximo<input name="max_price" type="number" min="0" step="any" value={filters.max_price} onChange={update} /></label></div>
          <label>Volumen promedio mínimo (títulos, 20 sesiones)<input name="min_volume" type="number" min="0" step="any" value={filters.min_volume} onChange={update} /></label>
          <div className="technical-filter-pair"><label>RSI mínimo<input name="rsi_min" type="number" min="0" max="100" step="any" value={filters.rsi_min} onChange={update} /></label><label>RSI máximo<input name="rsi_max" type="number" min="0" max="100" step="any" value={filters.rsi_max} onChange={update} /></label></div>
          <label>Relación EMA 50 / 200<select name="ema" value={filters.ema} onChange={update}><option value="">Todas</option><option value="above">EMA 50 mayor</option><option value="below">EMA 50 menor</option></select></label>
          <label>Antigüedad máxima (días naturales)<input name="max_age" type="number" min="0" max="36500" value={filters.max_age} onChange={update} /></label>
          <div className="actions"><button type="submit" disabled={state.status === "loading"}>Aplicar filtros</button><button className="ghost" type="button" onClick={clear}>Limpiar</button></div>
        </form>
      </details>
      <details ref={resultsRef} className="technical-results" open>
        <summary>Resultados {result ? `(${result.rows.length})` : ""}</summary>
        <p className="muted small">Cierres históricos, no tiempo real.</p>
        {state.status === "idle" && <p className="muted small">Aplica filtros para explorar.</p>}
        {state.status === "loading" && <p role="status">Calculando filtros…</p>}
        {state.status === "error" && <p role="alert" className="notice warn">{state.message}</p>}
        {result && <>
          {!result.rows.length && <p className="muted small">Ninguna emisora coincide con estos filtros.</p>}
          <motion.ul className="technical-result-list" initial={{ opacity: 0.85 }} animate={{ opacity: 1 }}>{result.rows.map((r) => <li key={r.instrument_id}>
            <button className="technical-result" type="button" onClick={() => onSelect(r.instrument_id)}>
              <strong>{r.ticker}</strong><span>{Number(r.last_close).toLocaleString("en-US", { maximumFractionDigits: 2 })} {r.currency}</span>
              <span className={r.change_pct == null ? "" : r.change_pct >= 0 ? "pos" : "neg"}>{r.change_pct == null ? "—" : `${r.change_pct >= 0 ? "+" : ""}${r.change_pct.toFixed(2)}%`}</span>
              <small className={r.freshness.stale ? "technical-stale" : "muted"}>{r.freshness.provider} · {r.freshness.last_bar} · {r.freshness.age_days} d{r.freshness.stale ? " · desactualizado" : ""}</small>
            </button>
          </li>)}</motion.ul>
          {result.unavailable.length > 0 && <details className="technical-unavailable"><summary>{result.unavailable.length} sin datos suficientes</summary><ul>{result.unavailable.map((r) => <li key={r.instrument_id}>{r.ticker}: {r.reason}</li>)}</ul></details>}
        </>}
      </details>
    </>
  );
}
