"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "motion/react";
import { api } from "../lib/api";

export default function SymbolSearch({ symbols, onSelect, onLoaded }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [watchlist, setWatchlist] = useState([]);
  const [remote, setRemote] = useState({ status: "idle", results: [], warnings: [] });
  const [chosen, setChosen] = useState(null);
  const [matches, setMatches] = useState(null);
  const [loaded, setLoaded] = useState(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef(null);
  const triggerRef = useRef(null);
  const searchRef = useRef(null);

  useEffect(() => {
    if (open && dialogRef.current && !dialogRef.current.open) {
      dialogRef.current.showModal();
      searchRef.current?.focus();
    }
  }, [open]);

  function closeSearch() {
    setOpen(false);
    requestAnimationFrame(() => triggerRef.current?.focus());
  }

  useEffect(() => {
    let active = true;
    api("/api/v1/technical/watchlist").then((items) => { if (active) setWatchlist(items); }).catch(() => { if (active) setMessage("No se pudo cargar seguimiento."); });
    return () => { active = false; };
  }, []);

  const local = useMemo(() => {
    const q = query.trim().toLocaleLowerCase();
    return (symbols ?? []).filter((s) => !q || `${s.ticker} ${s.name} ${s.market || ""}`.toLocaleLowerCase().includes(q));
  }, [symbols, query]);
  const ids = new Set(watchlist.map((x) => x.instrument_id));
  const watched = watchlist.filter((s) => !query.trim() || `${s.ticker} ${s.name} ${s.market || ""}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const groups = [["Cartera", local.filter((s) => s.held)], ["Seguimiento", watched], ["Catálogo", local.filter((s) => !s.held && !ids.has(s.instrument_id))]];

  async function search(event) {
    event.preventDefault();
    if (query.trim().length < 2) { setMessage("Escribe al menos dos caracteres para buscar en el mercado."); return; }
    setMessage(""); setChosen(null); setRemote({ status: "loading", results: [], warnings: [] });
    try { const data = await api(`/api/v1/technical/market-search?q=${encodeURIComponent(query.trim())}`); setRemote({ status: "ready", ...data }); }
    catch (err) { setRemote({ status: "error", results: [], warnings: [] }); setMessage(err.message); }
  }

  async function toggle(item) {
    setBusy(true); setMessage("");
    try {
      if (ids.has(item.instrument_id)) {
        await api(`/api/v1/technical/watchlist/${item.instrument_id}`, { method: "DELETE" });
        setWatchlist((list) => list.filter((x) => x.instrument_id !== item.instrument_id));
      } else {
        await api("/api/v1/technical/watchlist", { method: "POST", body: { instrument_id: item.instrument_id } });
        setWatchlist((list) => [...list, item]);
      }
    } catch (err) { setMessage(err.message); }
    finally { setBusy(false); }
  }

  async function load(selection = {}) {
    setBusy(true); setMessage("");
    try {
      const result = await api("/api/v1/technical/market-history", { method: "POST", body: { token: chosen.token, ...selection } });
      if (result.requires_selection) { setMatches(result.candidates); return; }
      await onLoaded(result.instrument_id);
      setMessage(`Historial cargado · ${result.provider} · última vela ${result.last_bar}.`);
      setLoaded(result); setChosen(null); setMatches(null);
    } catch (err) { setMessage(err.message); }
    finally { setBusy(false); }
  }

  async function includeInQuant() {
    setBusy(true); setMessage("");
    try { await api("/api/v1/quant/universe", { method: "POST", body: { instrument_id: loaded.instrument_id } }); setMessage(`${loaded.ticker} ya está habilitada en el universo de Quant Lab.`); setLoaded(null); }
    catch (err) { setMessage(err.message); }
    finally { setBusy(false); }
  }

  return <div className="symbol-search">
    <button ref={triggerRef} type="button" className="ghost symbol-search-trigger" onClick={() => setOpen(true)} aria-expanded={open} aria-controls="symbol-search-panel">⌕ Buscar emisora</button>
    {open && <dialog ref={dialogRef} id="symbol-search-panel" className="symbol-search-dialog" aria-labelledby="symbol-search-title" onCancel={(event) => { event.preventDefault(); closeSearch(); }} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); closeSearch(); } }} onClick={(event) => { if (event.target === dialogRef.current) closeSearch(); }}>
    <motion.section className="symbol-search-panel" initial={{ opacity: 0.86 }} animate={{ opacity: 1 }}>
      <header className="symbol-search-header"><div><h2 id="symbol-search-title">Buscar emisora</h2><p>Explora tu cartera, seguimiento y mercado</p></div><button type="button" className="symbol-search-close ghost" onClick={closeSearch} aria-label="Cerrar buscador">×</button></header>
      <form onSubmit={search} className="symbol-search-form">
        <label htmlFor="symbol-query">Buscar por símbolo o nombre</label>
        <div className="symbol-search-input"><input ref={searchRef} id="symbol-query" type="search" value={query} onChange={(e) => { setQuery(e.target.value); setRemote({ status: "idle", results: [], warnings: [] }); setChosen(null); }} placeholder="Ej. AAPL o Apple" /><button type="submit" disabled={remote.status === "loading"}>Buscar mercado</button></div>
      </form>
      <p className="muted small">La búsqueda en mercado consulta proveedores externos. Los históricos se cargan solo a petición.</p>
      {message && <p role="status" className="notice">{message}</p>}
      <div className="symbol-search-scroll">
        {groups.map(([label, items]) => <section key={label}><h3>{label} <span className="muted">{items.length}</span></h3>
          {!items.length && <p className="muted small">Sin coincidencias.</p>}
          {items.map((item) => <div className="symbol-search-row" key={item.instrument_id}>
            <button type="button" className="symbol-search-choice" onClick={() => { onSelect(item.instrument_id); closeSearch(); }}><strong>{item.ticker}</strong><span>{item.name} · {item.market || "mercado no indicado"} · {item.currency}</span></button>
            <button type="button" className="ghost symbol-star" disabled={busy} aria-label={`${ids.has(item.instrument_id) ? "Quitar de" : "Agregar a"} seguimiento ${item.ticker}`} aria-pressed={ids.has(item.instrument_id)} onClick={() => toggle(item)}>{ids.has(item.instrument_id) ? "★" : "☆"}</button>
          </div>)}
        </section>)}
        <section><h3>Mercado</h3>
          {remote.status === "idle" && <p className="muted small">Busca en proveedores para ver más emisoras.</p>}
          {remote.status === "loading" && <p role="status">Buscando en el mercado…</p>}
          {remote.status === "ready" && !remote.results.length && <p className="muted small">Sin coincidencias disponibles.</p>}
          {remote.warnings.map((warning) => <p className="technical-stale small" key={warning}>{warning}</p>)}
          {remote.results.map((item) => <button className="symbol-search-choice symbol-market-row" type="button" key={`${item.symbol}-${item.market}`} onClick={() => { setChosen(item); setMatches(null); setLoaded(null); }}><strong>{item.symbol}</strong><span>{item.name} · {item.market} · {item.currency || "moneda por confirmar"} · {item.source}</span></button>)}
        </section>
      </div>
      {chosen && <div className="symbol-search-selected"><strong>{chosen.symbol} · {chosen.market}</strong><span>{chosen.name} · {chosen.source}</span>{!matches && <button type="button" disabled={busy} onClick={() => load()}>{busy ? "Cargando historial…" : "Cargar historial"}</button>}{matches && <><span>Hay emisoras con el mismo símbolo y moneda. Elige una solo si corresponde al mismo instrumento; de otro modo crea una entrada separada.</span>{matches.map((item) => <button className="ghost" key={item.instrument_id} type="button" disabled={busy} onClick={() => load({ instrument_id: item.instrument_id })}>{item.name} · {item.market || "mercado no indicado"} · {item.currency}</button>)}<button className="ghost" type="button" disabled={busy} onClick={() => load({ create_new: true })}>Es otro instrumento</button></>}</div>}
      {loaded && <div className="symbol-search-selected"><span>El historial está aislado de Quant Lab. Puedes incorporarlo expresamente al laboratorio.</span><button type="button" className="ghost" disabled={busy} onClick={includeInQuant}>Habilitar en Quant Lab</button></div>}
    </motion.section></dialog>}
  </div>;
}
