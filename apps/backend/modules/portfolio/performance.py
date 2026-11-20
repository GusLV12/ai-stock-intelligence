"""Rendimiento ponderado por dinero (XIRR) del libro.

Solo se calcula cuando es EVALUABLE; si no, devuelve la razón (plan 6.3 y 14: "No evaluable",
nunca un número que sugiera algo falso). Es una métrica analítica en float, no un asiento contable.
"""

from datetime import datetime
from decimal import Decimal

from .models import Transaction

MIN_DAYS = 30  # anualizar periodos muy cortos produce cifras absurdas
SECONDS_PER_YEAR = 365 * 86400


def xirr(flows: list[tuple[datetime, float]]) -> float | None:
    """Tasa anual r tal que Σ cf / (1+r)^(años) = 0. None si no hay solución (sin cambio de signo)."""
    if len(flows) < 2:
        return None
    flows = sorted(flows, key=lambda f: f[0])
    t0 = flows[0][0]

    def npv(rate: float) -> float:
        return sum(cf / (1.0 + rate) ** ((t - t0).total_seconds() / SECONDS_PER_YEAR) for t, cf in flows)

    lo, hi = -0.9999, 1000.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo == 0:
        return lo
    if f_lo * f_hi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def money_weighted_return(
    account_ids, currency: str, terminal_value: Decimal | None, terminal_at, priced_all: bool
):
    """Devuelve {'xirr_pct': str|None, 'reason': str|None} para las cuentas y moneda dados."""
    txs = list(
        Transaction.objects.filter(
            account_id__in=account_ids, currency=currency, status=Transaction.Status.CONFIRMED
        ).order_by("executed_at", "id")
    )
    if not txs:
        return {"xirr_pct": None, "reason": "Sin operaciones."}
    if any(t.origin == "OPENING" for t in txs):
        return {
            "xirr_pct": None,
            "reason": "No evaluable: hay saldo de apertura sin fechas reales de compra.",
        }
    if not priced_all or terminal_value is None:
        return {"xirr_pct": None, "reason": "No evaluable: hay posiciones sin precio."}
    if terminal_at < txs[-1].executed_at:
        return {"xirr_pct": None, "reason": "No evaluable: hay operaciones posteriores a los precios."}
    flows = []
    for t in txs:
        gross = t.quantity * t.price_per_unit
        amount = -(gross + t.fees) if t.operation == Transaction.Operation.BUY else gross - t.fees
        flows.append((t.executed_at, float(amount)))
    flows.append((terminal_at, float(terminal_value)))
    span_days = (max(f[0] for f in flows) - min(f[0] for f in flows)).days
    if span_days < MIN_DAYS:
        return {"xirr_pct": None, "reason": f"Periodo menor a {MIN_DAYS} días: no se anualiza."}
    rate = xirr(flows)
    if rate is None:
        return {"xirr_pct": None, "reason": "No evaluable: los flujos no tienen solución."}
    return {"xirr_pct": f"{rate * 100:.2f}", "reason": None}
