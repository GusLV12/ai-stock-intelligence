"""Backtest de una estrategia long-only de ranking con fricción de mercado.

Cada `rebalance_days` sesiones se ordenan las emisoras por puntaje de la señal (generada al cierre de t) y se compran
las `top_k` a partes iguales al cierre de t+`delay`. Costos por lado sobre el valor operado: comisión + medio spread +
deslizamiento (en puntos base). Límite de liquidez: una posición no pasa de `max_adv_participation` del volumen medio
diario en dinero (20 sesiones); lo que no cabe queda en efectivo. Si no hay volumen no se puede verificar el límite y
se declara en el resultado. Referencias: el mismo universo en partes iguales sin selección, con rebalanceo idéntico y
con los mismos costos (aísla el valor de elegir), y compra y mantén una vez.

Los cálculos son estadísticos (float), no contables. Rendimiento anualizado con 252 sesiones, Sharpe con tasa libre 0.
No modela impuestos, ventas en corto, ni el tipo de cambio a MXN."""

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CostModel:
    commission_bps: float = 1.0
    half_spread_bps: float = 2.0
    slippage_bps: float = 2.0

    @property
    def per_side(self) -> float:
        return (self.commission_bps + self.half_spread_bps + self.slippage_bps) / 10_000


@dataclass(frozen=True)
class BacktestConfig:
    top_k: int = 3
    rebalance_days: int = 5
    delay: int = 1
    capital: float = 100_000.0
    max_adv_participation: float = 0.05
    costs: CostModel = CostModel()


def _closes_by_date(series):
    return {inst: {b["date"]: float(b["close"]) for b in bars} for inst, bars in series.items()}


def _adv(series, inst, upto_date, window=20):
    """Volumen medio en dinero de las últimas `window` sesiones hasta `upto_date` (None si falta algún volumen)."""
    bars = [b for b in series[inst] if b["date"] <= upto_date][-window:]
    if len(bars) < window or any(b.get("volume") is None for b in bars):
        return None
    return sum(float(b["close"]) * b["volume"] for b in bars) / window


def _equity_stats(equity: list[float]) -> dict:
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
    n = len(rets)
    total = equity[-1] / equity[0] - 1
    if n < 2:
        return {
            "total_return": total,
            "annual_return": None,
            "annual_vol": None,
            "sharpe": None,
            "max_drawdown": 0.0,
        }
    m = sum(rets) / n
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (n - 1))
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    ann = (equity[-1] / equity[0]) ** (252 / n) - 1 if equity[-1] > 0 else -1.0
    return {"total_return": total, "annual_return": ann, "annual_vol": sd * math.sqrt(252),
            "sharpe": (m / sd) * math.sqrt(252) if sd else None, "max_drawdown": mdd}  # fmt: skip


def _simulate(series, calendar, picks_at, cfg, hold_only_once=False):
    """Simula con una función `picks_at(d) -> [inst] | None` (None = no rebalancear). Devuelve curva y estadísticas de operación."""
    px = _closes_by_date(series)
    cash, shares = cfg.capital, {}
    equity, turnover, costs, liquidity_capped, last = [], 0.0, 0.0, 0, {}

    def price(inst, d):
        v = px[inst].get(d)
        if v is not None:
            last[inst] = v
        return last.get(inst)

    n_reb = 0
    for d in calendar:
        value = cash + sum(q * (price(i, d) or 0.0) for i, q in shares.items())
        target = picks_at(d)
        if target is not None and (not hold_only_once or n_reb == 0):
            n_reb += 1
            # objetivo en dinero por posición; se vende lo que sobra y se compra lo que falta, con costo por lado
            names = [i for i in target if price(i, d)]
            w = value / (1 + cfg.costs.per_side) / len(names) if names else 0.0
            goal = {}
            for i in names:
                cap = w
                adv = _adv(series, i, d)
                if adv is not None:
                    cap = min(w, cfg.max_adv_participation * adv)
                    liquidity_capped += cap < w
                goal[i] = cap
            for i in set(shares) | set(goal):
                p = price(i, d)
                if not p:
                    continue
                cur = shares.get(i, 0.0) * p
                diff = goal.get(i, 0.0) - cur
                fee = abs(diff) * cfg.costs.per_side
                cash -= diff + fee
                costs += fee
                turnover += abs(diff)
                shares[i] = goal.get(i, 0.0) / p
                if shares[i] == 0:
                    del shares[i]
            value = cash + sum(q * (price(i, d) or 0.0) for i, q in shares.items())
        equity.append(value)
    years = max(len(calendar) / 252, 1e-9)
    return equity, {"cost_paid": costs, "cost_drag_pct_capital": costs / cfg.capital, "turnover_one_way_annual": turnover / cfg.capital / 2 / years,
                    "rebalances": n_reb, "liquidity_capped_positions": liquidity_capped}  # fmt: skip


def run_backtest(
    series: dict, scores_by_date: dict, test_dates: list, cfg: BacktestConfig = BacktestConfig()
) -> dict:
    """`scores_by_date`: {fecha: {inst: puntaje}} (señal al cierre de esa fecha). Opera en `test_dates` (+ delay)."""
    all_dates = sorted({b["date"] for bars in series.values() for b in bars})
    signal_dates = [d for d in test_dates if d in scores_by_date]
    if len(signal_dates) < 2:
        raise ValueError("Muy pocas fechas de prueba para simular.")
    first_exec_idx = all_dates.index(signal_dates[0]) + cfg.delay
    last_signal_idx = all_dates.index(signal_dates[-1])
    calendar = all_dates[first_exec_idx : last_signal_idx + 1 + cfg.delay]
    if len(calendar) < 3:
        raise ValueError("Calendario de simulación demasiado corto.")
    exec_to_signal = {
        all_dates[all_dates.index(d) + cfg.delay]: d
        for d in signal_dates
        if all_dates.index(d) + cfg.delay < len(all_dates)
    }
    order = sorted(exec_to_signal)
    rebalance_at = set(order[:: cfg.rebalance_days])

    def model_picks(d):
        if d not in rebalance_at:
            return None
        sc = scores_by_date[exec_to_signal[d]]
        return [i for i, _ in sorted(sc.items(), key=lambda kv: (-kv[1], kv[0]))[: cfg.top_k]]

    universe = sorted({i for sc in scores_by_date.values() for i in sc})

    def ew_picks(d):
        return universe if d in rebalance_at else None

    eq_m, st_m = _simulate(series, calendar, model_picks, cfg)
    eq_ew, st_ew = _simulate(series, calendar, ew_picks, cfg)
    eq_bh, st_bh = _simulate(
        series, calendar, lambda d: universe if d == calendar[0] else None, cfg, hold_only_once=True
    )
    advs = [_adv(series, i, calendar[0]) for i in universe]
    return {
        "config": {**asdict(cfg), "universe_size": len(universe), "period": [str(calendar[0]), str(calendar[-1])], "sessions": len(calendar)},
        "dates": [d.isoformat() for d in calendar],
        "strategy": {"label": "Estrategia (top-K del modelo)", "equity": eq_m, **_equity_stats([cfg.capital, *eq_m]), **st_m},
        "benchmark_equal_weight": {"label": "Universo en partes iguales, mismo rebalanceo", "equity": eq_ew, **_equity_stats([cfg.capital, *eq_ew]), **st_ew},
        "benchmark_buy_hold": {"label": "Universo en partes iguales, compra y mantén", "equity": eq_bh, **_equity_stats([cfg.capital, *eq_bh]), **st_bh},
        "excess_vs_equal_weight": _equity_stats([cfg.capital, *eq_m])["total_return"] - _equity_stats([cfg.capital, *eq_ew])["total_return"],
        "liquidity_checked": all(a is not None for a in advs),
        "notes": ["Resultados netos de costos; no incluye impuestos ni tipo de cambio.",
                  "Una sola ventana de prueba: es una muestra, no una garantía."]
        + ([] if all(a is not None for a in advs) else ["Sin volumen suficiente: el límite de liquidez no pudo verificarse."]),
    }  # fmt: skip
