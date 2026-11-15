"""Centro fiscal PRELIMINAR: resumen contable de ventas por ejercicio.

No calcula impuestos ni ganancia fiscal en MXN (plan 14.5): esas cifras exigen clasificación
fiscal revisada, costo promedio actualizado y tipo de cambio. Aquí solo se muestran resultados
contables en la moneda original y qué falta para poder revisarlos con un contador.
"""

from collections import defaultdict
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Sum

from .models import LotDisposal, Transaction

MX = ZoneInfo("America/Mexico_City")
ZERO = Decimal(0)


def sales_for_year(user, year: int):
    """Ventas confirmadas del ejercicio (año calendario en hora de México) con su costo asignado."""
    out = []
    qs = Transaction.objects.filter(
        account__owner=user, operation=Transaction.Operation.SELL, status=Transaction.Status.CONFIRMED
    ).select_related("instrument", "account")
    for s in qs.order_by("executed_at", "id"):
        if s.executed_at.astimezone(MX).year != year:
            continue
        agg = LotDisposal.objects.filter(sale=s).aggregate(c=Sum("cost_basis"), n=Sum("net_proceeds"))
        cost, net = agg["c"] or ZERO, agg["n"] or ZERO
        pending = ["clasificación fiscal", "tipo de cambio a MXN", "costo fiscal actualizado"]
        if s.origin == "MANUAL" and not hasattr(s, "import_match"):
            pending.append("conciliación con estado de cuenta")
        out.append(
            {
                "id": s.id,
                "account": s.account.name,
                "instrument": s.instrument.name,
                "executed_at": s.executed_at,
                "quantity": s.quantity,
                "gross_proceeds": s.quantity * s.price_per_unit,
                "fees": s.fees,
                "cost_basis_book": cost,
                "realized_pnl_book": net - cost,
                "currency": s.currency,
                "origin": s.origin,
                "tax_status": "fiscal_pendiente",
                "pending": pending,
            }
        )
    return out


def year_summary(user, year: int) -> dict:
    sales = sales_for_year(user, year)
    by_cur = defaultdict(lambda: defaultdict(lambda: ZERO))
    for s in sales:
        t = by_cur[s["currency"]]
        t["gross_proceeds"] += s["gross_proceeds"]
        t["fees"] += s["fees"]
        t["cost_basis_book"] += s["cost_basis_book"]
        t["realized_pnl_book"] += s["realized_pnl_book"]
    unreconciled = sum(1 for s in sales if "conciliación con estado de cuenta" in s["pending"])
    return {
        "year": year,
        "sales_count": len(sales),
        "by_currency": {c: {k: str(v) for k, v in t.items()} for c, t in by_cur.items()},
        "tax_pending_count": len(sales),
        "unreconciled_manual_sales": unreconciled,
        "warning": (
            "Resumen contable en moneda original. No es ganancia fiscal ni ISR; requiere "
            "revisión de un contador (LISR art. 129, criterio SAT 37/ISR/N)."
        ),
    }
