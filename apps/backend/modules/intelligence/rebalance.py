"""Propuesta de rebalanceo (plan 8.4): compras y ventas TEÓRICAS para acercar la cartera a los pesos objetivo.

No son órdenes: no se envía nada a ningún broker. Reglas:
- Prioriza las aportaciones nuevas antes que vender (allow_sells=False por defecto).
- Las posiciones sin objetivo se tratan como fijas. El resto del valor objetivo queda en efectivo.
- Los títulos se redondean hacia abajo a la precisión del instrumento y el remanente queda en efectivo.
- No estima impuestos: una venta puede generar ganancia gravable (revisa el Centro fiscal).
"""

from decimal import ROUND_DOWN, Decimal

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)
TAX_NOTE = "No incluye impuestos: una venta puede generar ganancia gravable (revisa el Centro fiscal)."


class RebalanceError(ValueError):
    pass


def _shares(amount: Decimal, price: Decimal, scale: int) -> Decimal:
    return (amount / price).quantize(Decimal(1).scaleb(-scale), rounding=ROUND_DOWN)


def propose(
    holdings: list,
    targets_pct: dict[str, Decimal],
    contribution: Decimal = ZERO,
    allow_sells: bool = False,
    commission_per_trade: Decimal = ZERO,
) -> dict:
    """`holdings`: objetos Holding (value_base en USD). `targets_pct`: {ticker: 0–100}."""
    if contribution < 0 or commission_per_trade < 0:
        raise RebalanceError("La aportación y la comisión no pueden ser negativas.")
    if not targets_pct:
        raise RebalanceError("Indica pesos objetivo (o define una política con target_weights).")
    if sum(targets_pct.values(), ZERO) > HUNDRED:
        raise RebalanceError("Los pesos objetivo suman más de 100%.")
    warnings = [TAX_NOTE, "Propuesta teórica: no se envía ninguna orden."]

    by_ticker = {h.ticker: h for h in holdings if h.asset_class != "CASH"}
    cash_now = sum(
        (h.value_base for h in holdings if h.asset_class == "CASH" and h.value_base is not None), ZERO
    )
    tradable = {
        t: h for t, h in by_ticker.items() if h.value_base is not None and h.price and h.currency == "USD"
    }
    untradable = [t for t, h in by_ticker.items() if t not in tradable]
    if untradable:
        warnings.append(f"Sin precio o fuera de USD, se tratan como fijas: {', '.join(sorted(untradable))}.")
    unknown = [t for t in targets_pct if t not in tradable]
    if unknown:
        warnings.append(
            f"Objetivos ignorados (no tienes posición valorada en USD): {', '.join(sorted(unknown))}."
        )
    targets = {t: p for t, p in targets_pct.items() if t in tradable}

    fixed = {t: h.value_base for t, h in by_ticker.items() if h.value_base is not None and t not in targets}
    invested_now = sum((h.value_base for h in by_ticker.values() if h.value_base is not None), ZERO)
    total_now = invested_now + cash_now
    total_after = total_now + contribution
    if total_after <= 0:
        raise RebalanceError("No hay valor que rebalancear.")
    target_values = {t: p / HUNDRED * total_after for t, p in targets.items()}
    cash_target = total_after - sum(target_values.values(), ZERO) - sum(fixed.values(), ZERO)
    if cash_target < 0:
        raise RebalanceError("Los objetivos más las posiciones sin objetivo exceden el 100% del valor.")

    desired = {t: target_values[t] - tradable[t].value_base for t in targets}  # + compra, − venta
    buys = {t: d for t, d in desired.items() if d > 0}
    sells = {t: -d for t, d in desired.items() if d < 0} if allow_sells else {}
    available = contribution + cash_now - cash_target + sum(sells.values(), ZERO)
    if available < 0:
        available = ZERO
        warnings.append(
            "El efectivo disponible es menor que el objetivo de efectivo: no hay fondos para comprar."
        )
    need = sum(buys.values(), ZERO)
    # Comisión por operación: se descuenta del efectivo disponible (aprox., sobre las operaciones propuestas).
    n_trades = len(buys) + len(sells)
    commissions = commission_per_trade * n_trades
    available_net = max(available - commissions, ZERO)
    factor = ONE if need <= available_net else (available_net / need if need > 0 else ZERO)
    if factor < ONE and need > 0:
        warnings.append(
            f"Los fondos alcanzan para {factor:.0%} de las compras necesarias: queda una desviación residual."
        )

    trades, spent, received = [], ZERO, ZERO
    for t, h in sorted(tradable.items()):
        trade_amount = ZERO
        if t in buys:
            trade_amount = buys[t] * factor
        elif t in sells:
            trade_amount = -sells[t]
        side = "BUY" if trade_amount > 0 else ("SELL" if trade_amount < 0 else None)
        shares = ZERO
        if side:
            shares = _shares(abs(trade_amount), h.price, h.quantity_scale)
            if side == "SELL":
                shares = min(shares, h.quantity)
            exec_amount = shares * h.price
            if side == "BUY":
                spent += exec_amount
            else:
                received += exec_amount
            trade_amount = exec_amount if side == "BUY" else -exec_amount
            if shares == 0:
                side, trade_amount = None, ZERO
        post = h.value_base + trade_amount
        trades.append(
            {
                "ticker": t, "price": h.price, "current_value": h.value_base, "current_weight_pct": h.value_base / total_now * HUNDRED,
                "target_weight_pct": targets.get(t), "side": side, "shares": shares if side else ZERO,
                "amount": abs(trade_amount) if side else ZERO,
                "post_weight_pct": post / total_after * HUNDRED if t in targets else None,
                "drift_after_pct": (post / total_after * HUNDRED - targets[t]) if t in targets else None,
            }
        )  # fmt: skip
    real_trades = sum(1 for x in trades if x["side"])
    cash_after = cash_now + contribution - spent + received - commission_per_trade * real_trades
    drifts = [abs(x["drift_after_pct"]) for x in trades if x["drift_after_pct"] is not None]
    return {
        "total_before_usd": total_now,
        "contribution_usd": contribution,
        "total_after_usd": total_after,
        "allow_sells": allow_sells,
        "trades": trades,
        "summary": {
            "buys_usd": spent,
            "sells_usd": received,
            "estimated_commissions_usd": commission_per_trade * real_trades,
            "cash_before_usd": cash_now,
            "cash_after_usd": cash_after,
            "cash_target_usd": cash_target,
            "max_residual_drift_pct": max(drifts) if drifts else ZERO,
        },
        "warnings": warnings,
        "disclaimer": "Propuesta teórica con precios observados (no tiempo real); no es una orden ni asesoría de inversión.",
    }
