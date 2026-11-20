"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { api, apiDownloadGet, ApiError, fmtMoney, fmtSigned, signClass } from "../lib/api";
import SalesImport from "./SalesImport";

const THIS_YEAR = Number(new Intl.DateTimeFormat("en-US", { year: "numeric", timeZone: "America/Mexico_City" }).format(new Date()));
const YEARS = Array.from({ length: 6 }, (_, i) => THIS_YEAR - i);
const mxDate = (date) => new Date(date).toLocaleDateString("es-MX", { timeZone: "America/Mexico_City", year: "numeric", month: "short", day: "numeric" });
const quantityText = (value) => String(value).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");

function SaleDetail({ sale, close }) {
  const closeRef = useRef(null);
  const reduced = useReducedMotion();
  useEffect(() => {
    closeRef.current?.focus();
    const keydown = (event) => { if (event.key === "Escape") close(); };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [close, sale.id]);
  return <motion.aside className="sales-drawer" role="dialog" aria-modal="false" aria-labelledby="sales-detail-title"
    initial={reduced ? false : { x: 12, opacity: 0.98 }} animate={{ x: 0, opacity: 1 }}>
    <div className="sales-section-head"><div><h2 id="sales-detail-title">Detalle de la venta</h2><p className="muted">{sale.instrument} · {mxDate(sale.executed_at)}</p></div>
      <button ref={closeRef} type="button" className="ghost" onClick={close}>Cerrar</button></div>
    <p className="muted small">Cuenta: {sale.account} · Moneda: {sale.currency} · Títulos: {quantityText(sale.quantity)}</p>
    <h3>Desglose contable</h3>
    <dl className="sales-breakdown">
      <div><dt>Ingreso bruto</dt><dd>{fmtMoney(sale.gross_proceeds, sale.currency)}</dd></div>
      <div><dt>Comisión de venta</dt><dd>{fmtMoney(sale.fees, sale.currency)}</dd></div>
      <div><dt>Costo contable FIFO</dt><dd>{fmtMoney(sale.cost_basis_book, sale.currency)}</dd></div>
      <div><dt>Resultado contable</dt><dd className={signClass(sale.realized_pnl_book)}>{fmtSigned(sale.realized_pnl_book)} {sale.currency}</dd></div>
    </dl>
    <p className="muted small">Ingreso bruto − comisión − costo contable = resultado. No es ganancia fiscal ni cálculo de impuestos.</p>
    <h3>Por revisar</h3><ul className="sales-pending">{sale.pending.map((p) => <li key={p}>{p}</li>)}</ul>
  </motion.aside>;
}

export default function Ventas() {
  const router = useRouter();
  const [year, setYear] = useState(THIS_YEAR);
  const [account, setAccount] = useState("");
  const [accounts, setAccounts] = useState([]);
  const [accountsError, setAccountsError] = useState("");
  const [state, setState] = useState({ status: "loading" });
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  const [importing, setImporting] = useState(false);
  const [revision, setRevision] = useState(0);
  const [exportError, setExportError] = useState("");
  const triggerRef = useRef(null);

  useEffect(() => {
    let alive = true;
    api("/api/v1/accounts").then((data) => { if (alive) setAccounts(Array.isArray(data) ? data : data.results ?? []); })
      .catch((error) => { if (alive) setAccountsError(error.message); });
    return () => { alive = false; };
  }, []);
  useEffect(() => {
    let alive = true;
    const suffix = account ? `?account=${encodeURIComponent(account)}` : "";
    Promise.all([api(`/api/v1/tax/years/${year}/summary${suffix}`), api(`/api/v1/tax/years/${year}/sales${suffix}`)])
      .then(([summary, sales]) => { if (alive) setState({ status: "ready", summary, sales }); })
      .catch((error) => {
        if (!alive) return;
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) router.replace("/login");
        else setState({ status: "error", message: error.message });
      });
    return () => { alive = false; };
  }, [year, account, revision, router]);

  function resetFilter(setter, value) { setSelected(null); setState({ status: "loading" }); setter(value); }
  const closeDetail = useCallback(() => { setSelected(null); triggerRef.current?.focus(); }, []);
  async function download() {
    setExportError("");
    try {
      const suffix = account ? `?account=${encodeURIComponent(account)}` : "";
      const blob = await apiDownloadGet(`/api/v1/tax/years/${year}/export${suffix}`);
      const url = URL.createObjectURL(blob); const link = document.createElement("a");
      link.href = url; link.download = `ventas-${year}.csv`; link.click(); URL.revokeObjectURL(url);
    } catch (error) { setExportError(error.message); }
  }
  const sales = state.status === "ready" ? state.sales.filter((s) => `${s.ticker} ${s.instrument}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) : [];

  return <main className="wrap sales-page">
    <header className="top"><div><h1>Ventas por ejercicio</h1><p className="muted">Resumen contable preliminar; no es declaración ni cálculo de impuestos.</p></div>
      <div className="actions"><button type="button" className="ghost" onClick={() => setImporting((value) => !value)} aria-expanded={importing} aria-controls="sales-import-region">Importar movimientos</button>
        <button type="button" onClick={download} disabled={state.status !== "ready"}>Exportar resumen CSV</button></div></header>
    <div className="sales-filters"><label>Cuenta <select value={account} onChange={(e) => resetFilter(setAccount, e.target.value)}><option value="">Todas las cuentas</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label>Ejercicio (hora de México) <select value={year} onChange={(e) => resetFilter(setYear, Number(e.target.value))}>{YEARS.map((y) => <option key={y}>{y}</option>)}</select></label></div>
    {accountsError && <p role="alert" className="notice warn">No se pudieron cargar las cuentas: {accountsError}</p>}
    {exportError && <p role="alert" className="notice warn">No se pudo exportar: {exportError}</p>}
    <div id="sales-import-region">{importing && <SalesImport accounts={accounts} initialAccount={account} onClose={() => setImporting(false)} onConfirmed={() => { setSelected(null); setRevision((x) => x + 1); }} />}</div>
    {state.status === "loading" && <p role="status">Cargando…</p>}
    {state.status === "error" && <p role="alert" className="error">{state.message}</p>}
    {state.status === "ready" && <><p className="notice warn" role="note">{state.summary.warning}</p>
      {state.summary.sales_count === 0 ? <section className="card sales-empty"><h2>Sin ventas en {year}</h2>
        <p>Cuando registres o importes ventas de este ejercicio aparecerán aquí con su costo y pendientes.</p>
        <div className="actions"><button type="button" onClick={() => setImporting(true)}>Importar movimientos</button><Link className="btn ghost" href="/">Ver portafolio</Link></div></section>
        : <><section className="sales-summary" aria-label="Resumen del ejercicio">
          {Object.entries(state.summary.by_currency).map(([cur, t]) => <div className="sales-summary-currency" key={cur}>
            <div><span>Ventas · {cur}</span><strong>{fmtMoney(t.gross_proceeds, cur)}</strong></div>
            <div><span>Costo contable · {cur}</span><strong>{fmtMoney(t.cost_basis_book, cur)}</strong></div>
            <div><span>Resultado contable · {cur}</span><strong className={signClass(t.realized_pnl_book)}>{fmtSigned(t.realized_pnl_book)} {cur}</strong></div>
          </div>)}<div className="sales-count"><span>Operaciones</span><strong>{state.summary.sales_count}</strong><small>{state.summary.unreconciled_manual_sales} sin conciliar · {state.summary.tax_pending_count} con revisión fiscal</small></div>
        </section>
        <section className="sales-table-section"><div className="sales-section-head"><div><h2>Mesa de ventas</h2><p className="muted small">Selecciona una operación para ver su desglose.</p></div>
          <label>Buscar emisora <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Nombre o símbolo" /></label></div>
          <div className="tablewrap"><table><caption className="sr">Ventas del ejercicio {year}</caption><thead><tr><th>Fecha</th><th>Emisora</th><th>Moneda</th><th className="num">Títulos</th><th className="num">Ingreso bruto</th><th className="num">Comisión</th><th className="num">Costo FIFO</th><th className="num">Resultado contable</th><th>Pendientes</th><th>Detalle</th></tr></thead>
          <tbody>{sales.map((s) => <tr key={s.id}><td>{mxDate(s.executed_at)}</td><th scope="row"><button type="button" className="sales-row-trigger" onClick={(event) => { triggerRef.current = event.currentTarget; setSelected(s); }} aria-label={`Abrir detalle de ${s.ticker || s.instrument} del ${mxDate(s.executed_at)}`}>{s.ticker ? `${s.ticker} · ${s.instrument}` : s.instrument}</button></th><td>{s.currency}</td><td className="num">{quantityText(s.quantity)}</td><td className="num">{fmtMoney(s.gross_proceeds, s.currency)}</td><td className="num">{fmtMoney(s.fees, s.currency)}</td><td className="num">{fmtMoney(s.cost_basis_book, s.currency)}</td><td className={`num ${signClass(s.realized_pnl_book)}`}>{fmtSigned(s.realized_pnl_book)} {s.currency}</td><td>{s.pending.length} por revisar</td><td><button type="button" className="ghost" onClick={(event) => { triggerRef.current = event.currentTarget; setSelected(s); }} aria-label={`Ver detalle de ${s.ticker || s.instrument} del ${mxDate(s.executed_at)}`}>Ver detalle</button></td></tr>)}</tbody></table></div>
          {!sales.length && <p className="muted">No hay ventas que coincidan con la búsqueda.</p>}
        </section></>}
    </>}
    {selected && <SaleDetail sale={selected} close={closeDetail} />}
  </main>;
}
