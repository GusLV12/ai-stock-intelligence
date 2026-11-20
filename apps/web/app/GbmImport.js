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

  return (
    <MotionPanel className="portfolio-import" aria-label="Importar y conciliar GBM">
      <div className="portfolio-section-head">
        <div><h2>Actualizar con GBM</h2><p className="muted">Carga el detalle de portafolio USA en XLSX para compararlo con el libro.</p></div>
        <span className="portfolio-tag">1. Cargar · 2. Comparar</span>
      </div>
      <p className="notice">Al cargar se guarda el reporte y se registran sus precios; no se modifican las cantidades del libro. Las diferencias requieren revisión.</p>
      <form className="portfolio-import-form" onSubmit={upload}>
        <label>Cuenta GBM
          <select value={account} onChange={(e) => { setAccount(e.target.value); setResult(null); }} required>
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
      <div className="portfolio-export"><div><h3>Exportar movimientos</h3><p className="muted small">CSV para volver a importar o JSON para consulta. No sustituyen un respaldo completo.</p></div>
        <div className="actions"><button type="button" className="ghost" disabled={!account} onClick={() => download("csv")}>Descargar CSV</button>
          <button type="button" className="ghost" disabled={!account} onClick={() => download("json")}>Descargar JSON</button></div>
      </div>
    </MotionPanel>
  );
}
