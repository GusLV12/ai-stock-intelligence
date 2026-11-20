"use client";

import { useState } from "react";
import { api, apiUpload } from "../lib/api";
import { MotionPanel } from "../lib/Motion";

const LABELS = { VALID: "Lista para importar", DUPLICATE: "Duplicada", MATCH_MANUAL: "Coincide con captura manual", INVALID: "Inválida", DISCREPANCY: "Discrepancia", CONFIRMED: "Importada", LINKED: "Vinculada" };

export default function SalesImport({ accounts, initialAccount, onConfirmed, onClose }) {
  const [accountChoice, setAccountChoice] = useState(null);
  const account = accountChoice ?? (initialAccount || (accounts.length === 1 ? String(accounts[0].id) : ""));
  const [file, setFile] = useState(null);
  const [batch, setBatch] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  async function preview(event) {
    event.preventDefault();
    if (!account || !file) return;
    setAccountChoice(account);
    setBusy(true); setError(""); setBatch(null); setResult(null);
    try { setBatch(await apiUpload("/api/v1/imports", file, { account, resume_preview: "true" })); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function confirm() {
    if (!batch || String(batch.account) !== String(account)) {
      setError("La cuenta seleccionada no coincide con la del lote. Vuelve a revisar el archivo.");
      return;
    }
    setBusy(true); setError("");
    try {
      const data = await api(`/api/v1/imports/${batch.id}/confirm`, { method: "POST" });
      setResult(data.result); setBatch(data.batch); onConfirmed();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  const blocking = batch?.rows?.some((r) => ["INVALID", "DISCREPANCY"].includes(r.status));
  return <MotionPanel className="sales-import" aria-label="Importar movimientos">
    <div className="sales-section-head"><div><h2>Importar movimientos</h2><p className="muted">CSV canónico · revisa el contenido antes de confirmar.</p></div>
      <button type="button" className="ghost" onClick={onClose}>Cerrar</button></div>
    <p className="notice">El XLSX de posiciones de GBM no contiene el historial de ventas. El CSV requiere: operation, ticker, quantity, price y executed_at con zona horaria; fees, currency y broker_reference son opcionales. Las operaciones usan BUY o SELL.</p>
    <form onSubmit={preview} className="sales-import-form">
      <label>Cuenta <select value={account} disabled={busy} onChange={(e) => { setAccountChoice(e.target.value); setBatch(null); setResult(null); }} required>
        <option value="">Selecciona una cuenta</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
      </select></label>
      <label>Archivo CSV <input type="file" accept=".csv,text/csv" disabled={busy} required onChange={(e) => { setFile(e.target.files?.[0] ?? null); setBatch(null); setResult(null); }} /></label>
      <button type="submit" disabled={busy || !account || !file}>{busy ? "Procesando…" : "Revisar archivo"}</button>
    </form>
    {error && <p role="alert" className="notice warn">{error}</p>}
    {batch?.rows && <section aria-label="Vista previa de importación">
      <h3>Vista previa</h3><p className="muted small">La revisión guarda el lote, pero todavía no crea movimientos. Cuenta del lote: {accounts.find((a) => String(a.id) === String(batch.account))?.name ?? batch.account}.</p>
      <div className="tablewrap"><table><caption className="sr">Filas del archivo</caption><thead><tr><th>Fila</th><th>Operación</th><th>Emisora</th><th>Cantidad</th><th>Precio</th><th>Comisión</th><th>Moneda</th><th>Fecha y zona horaria</th><th>Estado</th><th>Detalle</th></tr></thead>
        <tbody>{batch.rows.map((row) => <tr key={row.row}><td>{row.row}</td><td>{row.data?.operation ?? "—"}</td><td>{row.ticker || "—"}</td><td>{row.data?.quantity ?? "—"}</td><td>{row.data?.price ?? "—"}</td><td>{row.data?.fees ?? "—"}</td><td>{row.data?.currency ?? "—"}</td><td>{row.data?.executed_at ?? "—"}</td><td>{LABELS[row.status] ?? row.status}</td><td>{row.errors.join("; ") || "—"}</td></tr>)}</tbody>
      </table></div>
      {blocking && <p className="notice warn">Corrige las filas inválidas o con discrepancias en el archivo y vuelve a cargarlo.</p>}
      <button type="button" onClick={confirm} disabled={busy || blocking || String(batch.account) !== String(account) || batch.status !== "PREVIEW" || !batch.rows.length}>Confirmar movimientos</button>
    </section>}
    {result && <p role="status" className="notice">Importación confirmada: {result.created} creados, {result.linked} vinculados y {result.skipped_duplicates} duplicados omitidos.</p>}
  </MotionPanel>;
}
