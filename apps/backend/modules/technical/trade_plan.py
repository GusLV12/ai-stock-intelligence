"""Plan de operación con motor de riesgo (plan 9.4 y 9.6): calcula tamaño y relación recompensa/riesgo y
APRUEBA o BLOQUEA según límites del usuario. No coloca órdenes ni las prepara.

Limitaciones que siempre se informan: el stop puede ejecutarse a peor precio (gaps) y no incluye comisiones salvo
que se indiquen. Solo compras (largo): los cortos están fuera del alcance."""

from decimal import ROUND_DOWN, Decimal

ZERO = Decimal(0)
HUNDRED = Decimal(100)

DEFAULT_LIMITS = {
    "max_risk_per_trade_pct": Decimal(1),  # % del capital que se puede perder en una operación
    "max_position_pct": Decimal(25),  # % del capital en una sola posición
    "min_reward_risk": Decimal("1.5"),
    "max_daily_loss_pct": Decimal(2),  # % del capital de pérdida diaria acumulada permitida
}


class PlanError(ValueError):
    pass


def evaluate(
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
    capital: Decimal,
    risk_budget: Decimal | None = None,
    side: str = "BUY",
    shares_scale: int = 0,
    slippage_pct: Decimal = ZERO,
    commission_per_trade: Decimal = ZERO,
    daily_loss_so_far: Decimal = ZERO,
    limits: dict | None = None,
) -> dict:
    if side != "BUY":
        raise PlanError("Solo se evalúan compras (largo); los cortos están fuera del alcance.")
    if min(entry, stop, target, capital) <= 0:
        raise PlanError("Entrada, stop, objetivo y capital deben ser mayores que cero.")
    if not (stop < entry < target):
        raise PlanError("Para una compra se requiere stop < entrada < objetivo.")
    if not (0 <= shares_scale <= 6):
        raise PlanError("shares_scale debe estar entre 0 y 6.")
    if slippage_pct < 0 or commission_per_trade < 0 or daily_loss_so_far < 0:
        raise PlanError("Deslizamiento, comisión y pérdida diaria no pueden ser negativos.")
    lim = {**DEFAULT_LIMITS, **(limits or {})}

    slip = slippage_pct / HUNDRED
    eff_entry, eff_stop = entry * (1 + slip), stop * (1 - slip)  # peor caso: se entra más caro y se sale más barato
    risk_ps, reward_ps = entry - stop, target - entry
    eff_risk_ps = eff_entry - eff_stop  # las comisiones se suman al riesgo total, no por título
    budget = risk_budget if risk_budget is not None else capital * lim["max_risk_per_trade_pct"] / HUNDRED
    if budget <= 0:
        raise PlanError("El presupuesto de riesgo debe ser mayor que cero.")

    q = Decimal(1).scaleb(-shares_scale)
    shares_by_risk = (budget / eff_risk_ps).quantize(q, rounding=ROUND_DOWN)
    shares_by_capital = (capital / eff_entry).quantize(q, rounding=ROUND_DOWN)
    shares = min(shares_by_risk, shares_by_capital)
    limited_by = "capital" if shares_by_capital < shares_by_risk else "riesgo"
    position_value = shares * entry
    risk_total = shares * eff_risk_ps + 2 * commission_per_trade
    reward_total = shares * reward_ps - 2 * commission_per_trade
    rr = reward_ps / risk_ps
    eff_rr = reward_total / risk_total if risk_total > 0 else None

    checks = [
        {"limit": "min_reward_risk", "passed": rr >= lim["min_reward_risk"], "detail": f"R:R bruta {rr:.2f} (mínimo {lim['min_reward_risk']})."},
        {"limit": "max_risk_per_trade_pct", "passed": risk_total / capital * HUNDRED <= lim["max_risk_per_trade_pct"],
         "detail": f"Riesgo {risk_total / capital * HUNDRED:.2f}% del capital (máximo {lim['max_risk_per_trade_pct']}%)."},
        {"limit": "max_position_pct", "passed": position_value / capital * HUNDRED <= lim["max_position_pct"],
         "detail": f"Posición {position_value / capital * HUNDRED:.1f}% del capital (máximo {lim['max_position_pct']}%)."},
        {"limit": "max_daily_loss_pct", "passed": (daily_loss_so_far + risk_total) / capital * HUNDRED <= lim["max_daily_loss_pct"],
         "detail": f"Pérdida diaria posible {(daily_loss_so_far + risk_total) / capital * HUNDRED:.2f}% (máximo {lim['max_daily_loss_pct']}%)."},
        {"limit": "size", "passed": shares > 0, "detail": f"{shares} títulos." if shares > 0 else "El tamaño calculado es 0 títulos."},
    ]
    verdict = "PASS" if all(c["passed"] for c in checks) else "BLOCK"
    return {
        "verdict": verdict,
        "blocked_by": [c["limit"] for c in checks if not c["passed"]],
        "checks": checks,
        "shares": shares,
        "limited_by": limited_by,
        "position_value": position_value,
        "risk_per_share": risk_ps,
        "reward_per_share": reward_ps,
        "reward_risk_gross": rr,
        "reward_risk_net": eff_rr,
        "risk_total": risk_total,
        "reward_total": reward_total,
        "budget_used": budget,
        "limits": lim,
        "warnings": [
            "El stop no garantiza el precio de salida: en gaps o mercados rápidos puede ejecutarse mucho peor.",
            "Es aritmética sobre tus datos; no es una orden ni una recomendación.",
            *([] if commission_per_trade else ["No incluye comisiones."]),
            *([] if slippage_pct else ["No incluye deslizamiento (slippage)."]),
        ],
    }
