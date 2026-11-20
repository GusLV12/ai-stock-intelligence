"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, fmtMoney, fmtSigned, signClass } from "./lib/api";
import { MotionPanel } from "./lib/Motion";
import GbmImport from "./GbmImport";

const STALE_HOURS = 48;

/** La flecha de `fmtSigned` va en un span aparte para que no se dibuje más grande que las cifras. */
function Value({ children }) {
  const m = typeof children === "string" ? children.match(/^([▲▼•])\s(.*)$/) : null;
  if (!m) return children;
  return (
    <>
      <span className="arrow" aria-hidden="true">{m[1]}</span>
      {m[2]}
    </>
  );
}

function Stat({ label, value, sub, tone, featured = false }) {
  return (
    <div className={`portfolio-stat${featured ? " portfolio-stat-featured" : ""}`}>
      <div className="portfolio-stat-head"><span className="portfolio-stat-label">{label}</span></div>
      <strong className={`portfolio-stat-value ${tone ?? ""}`}><Value>{value}</Value></strong>
      {sub && <span className={`portfolio-stat-sub ${tone ?? ""}`}>{sub}</span>}
    </div>
  );
}

function signedMoney(value, currency) {
  return value == null ? "—" : `${fmtSigned(value)}\u00a0${currency}`;
}

function portfolioMoney(value, currency) {
  return value == null ? "—" : `${fmtMoney(value, currency)}\u00a0${currency}`;
}

const ALLOC_COLORS = ["#7c6cff", "#2dd4bf", "#f472b6", "#fbbf24", "#60a5fa"];
const ALLOC_REST = "#7b8498";

/** Peso de cada emisora (el mismo `weight_pct` de la tabla) en una barra apilada; el resto se agrupa. */
function Allocation({ positions }) {
  const rows = positions
    .filter((p) => p.weight_pct != null && Number(p.weight_pct) > 0)
    .map((p) => ({ ticker: p.ticker, pct: Number(p.weight_pct) }))
    .sort((a, b) => b.pct - a.pct);
  if (rows.length < 2) return null;
  const top = rows.slice(0, ALLOC_COLORS.length);
  const rest = rows.slice(ALLOC_COLORS.length).reduce((s, r) => s + r.pct, 0);
  const parts = top.map((r, i) => ({ ...r, color: ALLOC_COLORS[i] }));
  if (rest > 0) parts.push({ ticker: "Otras", pct: rest, color: ALLOC_REST });
  const summary = parts.map((r) => `${r.ticker} ${r.pct.toFixed(1)}%`).join(", ");
  return (
    <section className="card alloc" aria-label="Distribución por emisora">
      <h2>Dónde está tu dinero</h2>
      <div className="alloc-bar" role="img" aria-label={`Distribución: ${summary}`}>
        {parts.map((r) => (
          <span key={r.ticker} style={{ flexGrow: r.pct, background: r.color }} />
        ))}
      </div>
      <ul className="alloc-legend" aria-hidden="true">
        {parts.map((r) => (
          <li key={r.ticker}>
            <i style={{ background: r.color }} />
            <b>{r.ticker}</b>
            <span>{r.pct.toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function Dashboard() {
  const router = useRouter();
  const [state, setState] = useState({ status: "loading" });
  const [user, setUser] = useState(null);
  const [view, setView] = useState("posiciones");
  const [currency, setCurrency] = useState("");

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
  const currencies = Object.keys(data.totals_by_currency);
  const cur = currencies.includes(currency) ? currency : currencies[0];
  const t = cur ? data.totals_by_currency[cur] : null;
  const stale = data.prices.oldest_age_hours != null && data.prices.oldest_age_hours > STALE_HOURS;
  const perf = cur ? data.performance?.[cur] : null;
  const positions = data.positions.filter((p) => p.currency === cur);
  const grouped = new Map();
  for (const p of positions) {
    if (p.weight_pct == null || Number(p.weight_pct) <= 0) continue;
    grouped.set(p.ticker, (grouped.get(p.ticker) ?? 0) + Number(p.weight_pct));
  }
  const topPositions = [...grouped].sort((a, b) => b[1] - a[1]).slice(0, 3);

  async function reloadDashboard() {
    const refreshed = await api("/api/v1/portfolio/dashboard");
    setState({ status: "ready", data: refreshed });
  }

  return (
    <main className="wrap">
      <header className="top">
        <div>
          <h1>Mi portafolio</h1>
          <p className="muted">Sesión de {user?.username}</p>
        </div>
        <div className="actions">
          <button type="button" onClick={() => setView("gbm")}>Importar GBM</button>
          <button className="ghost" onClick={logout}>Cerrar sesión</button>
        </div>
      </header>

      {(data.prices.newest_at || data.unpriced_positions > 0) && (
        <p className={`notice ${stale ? "warn" : ""}`} role="status">
          {data.prices.newest_at ? <>Precios de {data.prices.providers.join(", ")} (no son tiempo real): el más reciente es del{" "}
          {new Date(data.prices.newest_at).toLocaleString("es-MX")} y el más antiguo tiene{" "}
          {data.prices.oldest_age_hours} h{stale ? " — desactualizados" : ""}.</> : "No hay precios disponibles."}
          {data.unpriced_positions > 0 && ` ${data.unpriced_positions} posición(es) sin precio.`}
          {data.prices.uses_fallback &&
            " Incluye una fuente alterna no oficial (Yahoo Finance): puede retrasarse, cambiar o fallar sin aviso; verifica contra tu broker."}
        </p>
      )}

      <nav className="portfolio-tabs" aria-label="Secciones del portafolio">
        <button type="button" aria-pressed={view === "posiciones"} onClick={() => setView("posiciones")}>Posiciones</button>
        <button type="button" aria-pressed={view === "gbm"} onClick={() => setView("gbm")}>Importar y exportar GBM</button>
      </nav>

      {view === "gbm" ? <GbmImport onImported={reloadDashboard} /> : (
      <MotionPanel className="portfolio-overview">
      {currencies.length > 1 && <label className="portfolio-currency">Moneda del resumen
        <select value={cur} onChange={(e) => setCurrency(e.target.value)}>{currencies.map((c) => <option key={c}>{c}</option>)}</select>
      </label>}

      {!t ? (
        <div className="card empty">
          <h2>Aún no hay posiciones</h2>
          <p>Importa tu reporte de GBM o captura una operación para ver tu portafolio aquí.</p>
        </div>
      ) : (
        <>
          <section className="portfolio-stats" aria-label="Resumen">
            <Stat label="Valor de mercado" value={portfolioMoney(t.market_value, cur)} featured />
            <Stat label="Costo" value={portfolioMoney(t.cost_basis, cur)} />
            <Stat
              label="Ganancia no realizada"
              value={signedMoney(t.unrealized_pnl, cur)}
              sub={t.unrealized_pct != null ? fmtSigned(t.unrealized_pct, "%") : null}
              tone={signClass(t.unrealized_pnl)}
            />
            <Stat label="Ganancia realizada" value={signedMoney(t.realized_pnl, cur)} tone={signClass(t.realized_pnl)} />
            <Stat label="Efectivo del reporte" value={portfolioMoney(t.cash, cur)} sub={t.cash_observed_at ? `${t.cash_complete ? "Reportado" : "Parcial"} · ${new Date(t.cash_observed_at).toLocaleString("es-MX")}; no incluye movimientos posteriores.` : "Sin reporte de efectivo."} />
            {cur === "USD" && data.fx && (
              <Stat
                label="USD en MXN (referencia)"
                value={portfolioMoney(data.fx.total_value_mxn, "MXN")}
                sub={`${data.fx.pair} ${data.fx.rate} · ${data.fx.provider} · ${new Date(data.fx.observed_at).toLocaleString("es-MX")}${data.fx.is_fallback ? " (respaldo no oficial)" : ""} · Incluye efectivo del reporte.`}
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

          {topPositions.length > 0 && <section className="portfolio-top" aria-label="Tres posiciones principales">
            <div className="portfolio-section-head"><div><h2>Tus 3 posiciones principales</h2><p className="muted small">Por peso de las posiciones con precio en {cur}; no son recomendaciones.</p></div></div>
            <div className="portfolio-top-grid">{topPositions.map(([ticker, weight], i) => <div className="portfolio-top-item" key={ticker}>
              <span className="portfolio-rank">{i + 1}</span><strong>{ticker}</strong><span className="portfolio-top-weight">{weight.toFixed(2)}% <small>del valor con precio</small></span>
            </div>)}</div>
          </section>}
          <Allocation positions={positions} />

          <div className="portfolio-main-grid">
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
                {positions.map((p, index) => (
                  <tr key={`${p.ticker}-${index}`}>
                    <th scope="row">{p.ticker}</th>
                    <td className="num">{p.quantity}</td>
                    <td className="num">{portfolioMoney(p.average_cost, p.currency)}</td>
                    <td className="num">
                      {portfolioMoney(p.market_price, p.currency)}
                      {p.price_provider && (
                        <small className="src">{p.price_provider} · {p.price_age_hours} h</small>
                      )}
                    </td>
                    <td className="num">{portfolioMoney(p.market_value, p.currency)}</td>
                    <td className={`num ${signClass(p.unrealized_pnl)}`}>{signedMoney(p.unrealized_pnl, p.currency)}</td>
                    <td className={`num ${signClass(p.unrealized_pct)}`}>{fmtSigned(p.unrealized_pct, "%")}</td>
                    <td className="num">{p.weight_pct == null ? "—" : `${p.weight_pct}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <aside className="portfolio-quality" aria-label="Calidad de datos">
            <h2>Calidad de datos</h2>
            <div className={`portfolio-quality-item ${stale || data.unpriced_positions ? "attention" : ""}`}>
              <strong>{!data.prices.newest_at ? "Sin precios" : stale ? "Precios desactualizados" : "Precios registrados"}</strong>
              <p>{data.unpriced_positions ? `${data.unpriced_positions} posición(es) sin precio. ` : ""}{data.prices.oldest_age_hours != null ? `Antigüedad máxima: ${data.prices.oldest_age_hours} h.` : "No hay observaciones disponibles."}</p>
            </div>
            <div className="portfolio-quality-item"><strong>Efectivo del reporte</strong><p>{t.cash_observed_at ? `Última referencia: ${new Date(t.cash_observed_at).toLocaleString("es-MX")}. No se actualiza con movimientos posteriores.` : "No hay un saldo reportado para esta moneda."}</p></div>
            {perf && <div className="portfolio-quality-item"><strong>{perf.xirr_pct == null ? "XIRR no evaluable" : "Rendimiento anual (XIRR)"}</strong><p>{perf.reason ?? "Calculado con los flujos registrados."}</p></div>}
          </aside>
          </div>
        </>
      )}
      </MotionPanel>)}
      <p className="muted small">{data.disclaimer}</p>
    </main>
  );
}
