"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, fmtMoney, fmtSigned, signClass } from "../lib/api";

const THIS_YEAR = new Date().getFullYear();
const YEARS = Array.from({ length: 6 }, (_, i) => THIS_YEAR - i);

export default function Ventas() {
  const router = useRouter();
  const [year, setYear] = useState(THIS_YEAR);
  const [state, setState] = useState({ status: "loading" });

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [summary, sales] = await Promise.all([
          api(`/api/v1/tax/years/${year}/summary`),
          api(`/api/v1/tax/years/${year}/sales`),
        ]);
        if (alive) setState({ status: "ready", summary, sales });
      } catch (err) {
        if (!alive) return;
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          router.replace("/login");
        } else {
          setState({ status: "error", message: err.message });
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [year, router]);

  return (
    <main className="wrap">
      <header className="top">
        <div>
          <h1>Ventas por ejercicio</h1>
          <p className="muted">Resumen contable preliminar; no es declaración ni cálculo de impuestos.</p>
        </div>
        <nav className="actions" aria-label="Principal">
          <Link className="btn ghost" href="/">Portafolio</Link>
        </nav>
      </header>

      <div className="row-actions">
        <label htmlFor="year">Ejercicio (hora de México)</label>
        <select id="year" value={year} onChange={(e) => {
            setState({ status: "loading" });
            setYear(Number(e.target.value));
          }}>
          {YEARS.map((y) => (
            <option key={y} value={y}>{y}</option>
          ))}
        </select>
      </div>

      {state.status === "loading" && <p role="status">Cargando…</p>}
      {state.status === "error" && <p role="alert" className="error">{state.message}</p>}

      {state.status === "ready" && (
        <>
          <p className="notice warn" role="note">{state.summary.warning}</p>

          {state.summary.sales_count === 0 ? (
            <div className="card empty">
              <h2>Sin ventas en {year}</h2>
              <p>Cuando registres o importes ventas de este ejercicio aparecerán aquí.</p>
            </div>
          ) : (
            <>
              <section className="stats" aria-label="Resumen del ejercicio">
                {Object.entries(state.summary.by_currency).map(([cur, t]) => (
                  <div className="card stat" key={cur}>
                    <span className="label">Ventas {cur}</span>
                    <strong>{fmtMoney(t.gross_proceeds, cur)}</strong>
                    <span className="sub">Costo {fmtMoney(t.cost_basis_book, cur)}</span>
                    <span className={`sub ${signClass(t.realized_pnl_book)}`}>
                      Contable {fmtSigned(t.realized_pnl_book)}
                    </span>
                  </div>
                ))}
                <div className="card stat">
                  <span className="label">Operaciones</span>
                  <strong>{state.summary.sales_count}</strong>
                  <span className="sub">{state.summary.tax_pending_count} con revisión fiscal pendiente</span>
                  <span className="sub">{state.summary.unreconciled_manual_sales} manual(es) sin conciliar</span>
                </div>
              </section>

              <div className="tablewrap">
                <table>
                  <caption className="sr">Ventas del ejercicio {year}</caption>
                  <thead>
                    <tr>
                      <th scope="col">Fecha</th>
                      <th scope="col">Emisora</th>
                      <th scope="col" className="num">Títulos</th>
                      <th scope="col" className="num">Ingreso bruto</th>
                      <th scope="col" className="num">Costo</th>
                      <th scope="col" className="num">Resultado contable</th>
                      <th scope="col">Pendiente</th>
                    </tr>
                  </thead>
                  <tbody>
                    {state.sales.map((s) => (
                      <tr key={s.id}>
                        <td>{new Date(s.executed_at).toLocaleDateString("es-MX")}</td>
                        <th scope="row">{s.instrument}</th>
                        <td className="num">{Number(s.quantity).toString()}</td>
                        <td className="num">{fmtMoney(s.gross_proceeds, s.currency)}</td>
                        <td className="num">{fmtMoney(s.cost_basis_book, s.currency)}</td>
                        <td className={`num ${signClass(s.realized_pnl_book)}`}>{fmtSigned(s.realized_pnl_book)}</td>
                        <td>
                          <ul className="pending-list">
                            {s.pending.map((p) => (<li key={p}>{p}</li>))}
                          </ul>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </main>
  );
}
