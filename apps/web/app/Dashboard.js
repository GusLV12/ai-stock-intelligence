"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, fmtMoney, fmtSigned, signClass } from "./lib/api";

const STALE_HOURS = 48;

function Stat({ label, value, sub, tone }) {
  return (
    <div className="card stat">
      <span className="label">{label}</span>
      <strong className={tone}>{value}</strong>
      {sub && <span className={`sub ${tone ?? ""}`}>{sub}</span>}
    </div>
  );
}

export default function Dashboard() {
  const router = useRouter();
  const [state, setState] = useState({ status: "loading" });
  const [user, setUser] = useState(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const me = await api("/api/v1/auth/me");
        const data = await api("/api/v1/portfolio/dashboard");
        if (alive) {
          setUser(me);
          setState({ status: "ready", data });
        }
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
  }, [router]);

  async function logout() {
    await api("/api/v1/auth/logout", { method: "POST" }).catch(() => {});
    router.replace("/login");
  }

  if (state.status === "loading") return <p className="center" role="status">Cargando…</p>;
  if (state.status === "error")
    return (
      <main className="wrap">
        <p role="alert" className="error">No se pudo cargar el dashboard: {state.message}</p>
      </main>
    );

  const { data } = state;
  const cur = Object.keys(data.totals_by_currency)[0];
  const t = cur ? data.totals_by_currency[cur] : null;
  const stale = data.prices.age_hours != null && data.prices.age_hours > STALE_HOURS;

  return (
    <main className="wrap">
      <header className="top">
        <div>
          <h1>Mi portafolio</h1>
          <p className="muted">Sesión de {user?.username}</p>
        </div>
        <button className="ghost" onClick={logout}>Cerrar sesión</button>
      </header>

      {data.prices.as_of && (
        <p className={`notice ${stale ? "warn" : ""}`} role="status">
          Precios: {data.prices.source} · {new Date(data.prices.as_of).toLocaleString("es-MX")}
          {" "}(hace {data.prices.age_hours} h){stale ? " — desactualizados" : ""}.
          {data.unpriced_positions > 0 && ` ${data.unpriced_positions} posición(es) sin precio en el reporte.`}
        </p>
      )}

      {!t ? (
        <div className="card empty">
          <h2>Aún no hay posiciones</h2>
          <p>Importa tu reporte de GBM o captura una operación para ver tu portafolio aquí.</p>
        </div>
      ) : (
        <>
          <section className="stats" aria-label="Resumen">
            <Stat label="Valor de mercado" value={fmtMoney(t.market_value, cur)} />
            <Stat label="Costo" value={fmtMoney(t.cost_basis, cur)} />
            <Stat
              label="Ganancia no realizada"
              value={fmtSigned(t.unrealized_pnl)}
              sub={t.unrealized_pct != null ? fmtSigned(t.unrealized_pct, "%") : null}
              tone={signClass(t.unrealized_pnl)}
            />
            <Stat label="Ganancia realizada" value={fmtSigned(t.realized_pnl)} tone={signClass(t.realized_pnl)} />
            <Stat label="Efectivo" value={fmtMoney(t.cash, cur)} />
          </section>

          <div className="tablewrap">
            <table>
              <caption className="sr">Posiciones</caption>
              <thead>
                <tr>
                  <th scope="col">Emisora</th>
                  <th scope="col" className="num">Títulos</th>
                  <th scope="col" className="num">Costo prom.</th>
                  <th scope="col" className="num">Precio</th>
                  <th scope="col" className="num">Valor</th>
                  <th scope="col" className="num">Ganancia</th>
                  <th scope="col" className="num">%</th>
                  <th scope="col" className="num">Peso</th>
                </tr>
              </thead>
              <tbody>
                {data.positions.map((p) => (
                  <tr key={p.ticker}>
                    <th scope="row">{p.ticker}</th>
                    <td className="num">{p.quantity}</td>
                    <td className="num">{fmtMoney(p.average_cost, p.currency)}</td>
                    <td className="num">{fmtMoney(p.market_price, p.currency)}</td>
                    <td className="num">{fmtMoney(p.market_value, p.currency)}</td>
                    <td className={`num ${signClass(p.unrealized_pnl)}`}>{fmtSigned(p.unrealized_pnl)}</td>
                    <td className={`num ${signClass(p.unrealized_pct)}`}>{fmtSigned(p.unrealized_pct, "%")}</td>
                    <td className="num">{p.weight_pct == null ? "—" : `${p.weight_pct}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <p className="muted small">{data.disclaimer}</p>
    </main>
  );
}
