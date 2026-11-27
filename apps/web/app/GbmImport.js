"use client";

import { useEffect, useState } from "react";
import { api, apiDownloadGet, apiUpload } from "./lib/api";
import { MotionPanel } from "./lib/Motion";

const LABELS = { ok: "Coincide", diferencia: "Diferencia", falta_en_libro: "Falta en el libro", falta_en_reporte: "Falta en el reporte" };

export default function GbmImport({ onImported }) {
  const [accounts, setAccounts] = useState([]);
  const [account, setAccount] = useState("");
  const [file, setFile] = useState(null);
  const [date, setDate] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState([]);
  const [pendingError, setPendingError] = useState("");
  const [pdfFiles, setPdfFiles] = useState([]);
  const [pdfPreview, setPdfPreview] = useState(null);
  const [fixRates, setFixRates] = useState({});

  async function loadPending(id) {
    if (!id) { setPending([]); return; }
    try {
      const data = await api(`/api/v1/imports/pending-sales?account=${encodeURIComponent(id)}`);
      setPending(data.pending);
      setPendingError("");
    } catch (e) { setPendingError(e.message); }
  }

  useEffect(() => {
    let active = true;
    if (!account) return;
    api(`/api/v1/imports/pending-sales?account=${encodeURIComponent(account)}`)
      .then((data) => { if (active) { setPending(data.pending); setPendingError(""); } })
      .catch((e) => { if (active) setPendingError(e.message); });
    return () => { active = false; };
  }, [account]);

  useEffect(() => {
    let active = true;
    api("/api/v1/accounts").then((data) => {
      if (!active) return;
      const list = (Array.isArray(data) ? data : data.results ?? []).filter((a) => a.broker === "GBM_USA");
      setAccounts(list);
      if (list.length === 1) setAccount(String(list[0].id));
    }).catch((e) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);

  async function upload(event) {
    event.preventDefault();
    if (!file || !account) return;
    setError(""); setResult(null); setBusy(true);
    try {
      const fields = { account };
      if (date) fields.as_of = new Date(date).toISOString();
      const data = await apiUpload("/api/v1/imports/positions", file, fields);
      setResult(data);
      await onImported();
      await loadPending(account);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function download(format) {
    setError("");
    try {
      const blob = await apiDownloadGet(`/api/v1/exports?account=${encodeURIComponent(account)}&format=${format}`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = `movimientos.${format}`; link.click();
      URL.revokeObjectURL(url);
    } catch (e) { setError(e.message); }
  }

  async function previewPdf(event) {
    event.preventDefault();
    if (!account || !pdfFiles.length) return;
    setBusy(true); setError(""); setPdfPreview(null);
    try {
      setPdfPreview(await apiUpload("/api/v1/imports/drivewealth/preview-many", pdfFiles, { account }));
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function lookupFix(day) {
    setFixRates((old) => ({ ...old, [day]: { loading: true } }));
    try {
      const data = await api("/api/v1/market/fx/historical-fix", { method: "POST", body: { date: day } });
      setFixRates((old) => ({ ...old, [day]: data }));
    } catch (e) {
      setFixRates((old) => ({ ...old, [day]: { reason: e.message } }));
    }
  }

  return (
    <MotionPanel className="portfolio-import" aria-label="Importar y conciliar GBM">
      <div className="portfolio-section-head">
        <div><h2>Actualizar con GBM</h2><p className="muted">Carga el detalle de portafolio USA en XLSX para compararlo con el libro.</p></div>
        <span className="portfolio-tag">1. Cargar · 2. Comparar</span>
      </div>
      <p className="notice">Al cargar se guarda el reporte y se registran sus precios; no se modifican las cantidades del libro. Las diferencias requieren revisión.</p>
      <form className="portfolio-import-form" onSubmit={upload}>
        <label>Cuenta GBM
          <select value={account} disabled={busy} onChange={(e) => { setAccount(e.target.value); setResult(null); setPdfPreview(null); setPending([]); }} required>
            <option value="">Selecciona una cuenta</option>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </label>
        <label>Archivo XLSX <input type="file" accept=".xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} required /></label>
        <label>Fecha del reporte <span className="muted">(solo si el nombre no la incluye)</span>
          <input type="datetime-local" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <button type="submit" disabled={busy || !account || !file}>{busy ? "Cargando…" : "Cargar y comparar"}</button>
      </form>
      {!accounts.length && !error && <p className="muted small">Necesitas una cuenta de GBM Trading USA para importar.</p>}
      {error && <p role="alert" className="notice warn">{error}</p>}
      {account && <section className="portfolio-reconciliation" aria-label="Cambios pendientes entre reportes">
        <h3>Posibles ventas pendientes</h3>
        <p className="muted small">Disminuciones entre reportes consecutivos. Pueden incluir transferencias u otros ajustes; no son ventas registradas ni modifican el libro. Aún no se descuentan los movimientos que ya estén registrados.</p>
        {pendingError && <p role="alert">No se pudieron consultar los pendientes: {pendingError}</p>}
        {!pendingError && !pending.length && <p>No se detectaron disminuciones entre reportes comparables. Reportes con la misma fecha no se comparan.</p>}
        {pending.length > 0 && <div className="tablewrap"><table><thead><tr><th>Emisora</th><th>Tipo</th><th>Disminución</th><th>Reporte anterior</th><th>Reporte posterior</th><th>Estado</th></tr></thead>
          <tbody>{pending.map((item) => <tr key={item.id}><th scope="row">{item.ticker}</th><td>{item.kind === "total" ? "Ausente en el reporte" : "Disminución parcial"}</td><td>{item.quantity}</td><td>{new Date(item.previous_as_of).toLocaleString("es-MX")}</td><td>{new Date(item.current_as_of).toLocaleString("es-MX")}</td><td>Pendiente de revisión</td></tr>)}</tbody>
        </table></div>}
      </section>}
      {result && <MotionPanel className="portfolio-reconciliation" role="status">
        <h3>Resultado de la conciliación</h3>
        <p className="muted small">Reporte del {new Date(result.as_of).toLocaleString("es-MX")}. No se crearon movimientos.</p>
        <div className="tablewrap"><table><caption className="sr">Comparación del reporte con el libro</caption>
          <thead><tr><th>Emisora</th><th>Reportado</th><th>En el libro</th><th>Diferencia</th><th>Estado</th></tr></thead>
          <tbody>{result.reconciliation.map((row, i) => <tr key={`${row.ticker}-${i}`}>
            <th scope="row">{row.ticker}</th><td>{row.reported_quantity}</td><td>{row.ledger_quantity}</td>
            <td>{row.quantity_diff}</td><td>{LABELS[row.status] ?? row.status}</td>
          </tr>)}</tbody>
        </table></div>
      </MotionPanel>}
      <section className="portfolio-reconciliation" aria-label="Revisar PDF DriveWealth">
        <h3>Revisar estado o confirmación de DriveWealth</h3>
        <p className="muted small">Vista previa únicamente: el documento no crea movimientos. El estado mensual y las confirmaciones pueden contener varias ejecuciones de una orden.</p>
        <form className="portfolio-import-form" onSubmit={previewPdf}>
          <label>Archivos PDF<input type="file" accept="application/pdf,.pdf" multiple onChange={(e) => { setPdfFiles(Array.from(e.target.files ?? [])); setPdfPreview(null); }} required /></label>
          <button type="submit" disabled={busy || !account || !pdfFiles.length}>{busy ? "Revisando…" : "Revisar PDF"}</button>
        </form>
        {pdfPreview?.documents.map((doc) => <div role="status" key={doc.index}>
          <h4>{doc.name}</h4>
          {doc.duplicate_of != null && <p className="notice warn">Este archivo es idéntico a otro de la selección.</p>}
          {doc.error ? <p role="alert">No se pudo revisar: {doc.error}</p> : <>
          <p>Documento: {doc.document_type === "confirmation" ? "confirmación de operaciones" : "estado de cuenta"}. {doc.rows.length} operaciones reconocidas; {doc.warnings.length} filas requieren revisión.</p>
          <p className="muted small">Las comisiones ausentes no son cero. La estimación del 0.25 % es una referencia y puede cambiar.</p>
          <p className="muted small">El FIX es diario y no acredita el tipo de cambio fiscal aplicable ni el de la hora de ejecución.</p>
          <div className="tablewrap"><table><thead><tr><th>Fecha</th><th>Liquidación</th><th>Tipo</th><th>Emisora</th><th>Títulos</th><th>Precio</th><th>Importe / neto</th><th>Comisión</th><th>Otros cargos / créditos</th><th>FIX fecha</th><th>Coincidencia posible</th></tr></thead>
            <tbody>{doc.rows.map((row) => <tr key={`${row.page}-${row.line}`}><td>{row.trade_date}</td><td>{row.settle_date}</td><td>{{BUY: "Compra", SELL: "Venta", DIV: "Dividendo", DIVNRA: "Retención"}[row.operation] ?? row.operation}{row.possible_duplicate && <small className="src">Posible duplicado; revisar</small>}</td><th scope="row">{row.ticker}</th><td>{row.quantity}</td><td>{row.price} {row.currency}{row.currency_inferred && <small className="src">Moneda por verificar</small>}</td><td>{row.net_amount ?? row.amount ?? "—"}</td><td>{row.commission ?? (row.fee_estimate ? `Estimada: ${row.fee_estimate}` : "No identificada")}</td><td>{row.transaction_fee ?? "—"} / {row.other_fees_credits ?? "—"}</td><td>{fixRates[row.trade_date]?.available ? `${fixRates[row.trade_date].rate} MXN/USD · ${row.trade_date}` : fixRates[row.trade_date]?.reason ?? <button type="button" disabled={fixRates[row.trade_date]?.loading} onClick={() => lookupFix(row.trade_date)}>{fixRates[row.trade_date]?.loading ? "Consultando…" : "Consultar"}</button>}</td><td>{row.candidates.length ? `${row.candidates.length} pendiente(s) por revisar` : "Sin coincidencia"}</td></tr>)}</tbody>
          </table></div>
          {doc.warnings.length > 0 && <p>Hay filas de actividad que el lector no interpretó; revisa el documento original antes de usar los datos.</p>}
          </>}
        </div>)}
      </section>
      <div className="portfolio-export"><div><h3>Exportar movimientos</h3><p className="muted small">CSV para volver a importar o JSON para consulta. No sustituyen un respaldo completo.</p></div>
        <div className="actions"><button type="button" className="ghost" disabled={!account} onClick={() => download("csv")}>Descargar CSV</button>
          <button type="button" className="ghost" disabled={!account} onClick={() => download("json")}>Descargar JSON</button></div>
      </div>
    </MotionPanel>
  );
}
