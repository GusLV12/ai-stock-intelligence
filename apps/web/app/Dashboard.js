"use client";

import Link from "next/link";
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
  const stale = data.prices.oldest_age_hours != null && data.prices.oldest_age_hours > STALE_HOURS;
  const perf = cur ? data.performance?.[cur] : null;

  return (
    <main className="wrap">
      <header className="top">
        <div>
          <h1>Mi portafolio</h1>
          <p className="muted">Sesión de {user?.username}</p>
        </div>
        <nav className="actions" aria-label="Principal">
          <Link className="btn ghost" href="/screener">Screener</Link>
          <Link className="btn ghost" href="/fair-value">Fair Value</Link>
          <Link className="btn ghost" href="/calculadoras">Calculadoras</Link>
          <Link className="btn ghost" href="/asistente">Asistente</Link>
          <Link className="btn ghost" href="/ventas">Ventas por ejercicio</Link>
          <button className="ghost" onClick={logout}>Cerrar sesión</button>
        </nav>
      </header>

      {data.prices.newest_at && (
        <p className={`notice ${stale ? "warn" : ""}`} role="status">
          Precios de {data.prices.providers.join(", ")} (no son tiempo real): el más reciente es del{" "}
          {new Date(data.prices.newest_at).toLocaleString("es-MX")} y el más antiguo tiene{" "}
          {data.prices.oldest_age_hours} h{stale ? " — desactualizados" : ""}.
          {data.unpriced_positions > 0 && ` ${data.unpriced_positions} posición(es) sin precio.`}
          {data.prices.uses_fallback &&
            " Incluye una fuente alterna no oficial (Yahoo Finance): puede retrasarse, cambiar o fallar sin aviso; verifica contra tu broker."}
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
            {data.fx && (
              <Stat
                label="Total en MXN (referencia)"
                value={fmtMoney(data.fx.total_value_mxn, "MXN")}
                sub={`${data.fx.pair} ${data.fx.rate} · ${data.fx.provider}${data.fx.is_fallback ? " (respaldo no oficial)" : ""}`}
              />
            )}
            {perf && (
              <Stat
                label="Rendimiento anual (XIRR)"
                value={perf.xirr_pct != null ? fmtSigned(perf.xirr_pct, "%") : "No evaluable"}
                sub={perf.reason}
                tone={perf.xirr_pct != null ? signClass(perf.xirr_pct) : undefined}
              />
            )}
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
                    <td className="num">
                      {fmtMoney(p.market_price, p.currency)}
                      {p.price_provider && (
                        <small className="src">{p.price_provider} · {p.price_age_hours} h</small>
                      )}
                    </td>
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
