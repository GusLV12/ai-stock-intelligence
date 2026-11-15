"""Datos del dashboard. Costos y cantidades salen del libro; los precios, del ÚLTIMO REPORTE del
broker (no hay proveedor de cotizaciones todavía), y se etiquetan con su fecha y antigüedad."""

from collections import defaultdict
from decimal import Decimal

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.imports.models import PositionSnapshot
from modules.instruments.models import InstrumentIdentifier

from .api import _positions_for

ZERO = Decimal(0)
CENT = Decimal("0.01")


def _q(v: Decimal | None, places=CENT):
    return None if v is None else str(v.quantize(places))


def build_dashboard(user) -> dict:
    now = timezone.now()
    snaps = {}  # cuenta -> último reporte
    for s in PositionSnapshot.objects.filter(account__owner=user).order_by("as_of"):
        snaps[s.account_id] = s
    lines = {(acc_id, ln.ticker): ln for acc_id, s in snaps.items() for ln in s.lines.all()}
    tickers = {i.instrument_id: i.value for i in InstrumentIdentifier.objects.filter(kind="TICKER")}

    rows = []
    for r in _positions_for(user):
        ticker = tickers.get(r["instrument"], r["instrument_name"])
        ln = lines.get((r["account"], ticker))
        price = ln.market_price if ln else None
        value = ln.market_value if ln else None
        cost = r["cost_basis"]
        rows.append(
            {
                "ticker": ticker,
                "currency": r["currency"],
                "quantity": r["quantity"],
                "average_cost": r["average_cost"],
                "cost_basis": cost,
                "market_price": price,
                "market_value": value,
                "unrealized_pnl": None if value is None else value - cost,
                "unrealized_pct": None if value is None or cost == 0 else (value - cost) / cost * 100,
                "realized_pnl": r["realized_pnl"],
            }
        )

    totals = defaultdict(lambda: defaultdict(lambda: ZERO))
    for row in rows:
        t = totals[row["currency"]]
        t["cost_basis"] += row["cost_basis"]
        t["realized_pnl"] += row["realized_pnl"]
        if row["market_value"] is not None:
            t["market_value"] += row["market_value"]
            t["priced_cost_basis"] += row["cost_basis"]
    cash = defaultdict(lambda: ZERO)
    for s in snaps.values():
        if s.cash_amount is not None:
            cash[s.cash_currency] += s.cash_amount

    out_totals = {}
    for cur, t in totals.items():
        mv, pc = t["market_value"], t["priced_cost_basis"]
        out_totals[cur] = {
            "cost_basis": _q(t["cost_basis"]),
            "market_value": _q(mv),
            "unrealized_pnl": _q(mv - pc),
            "unrealized_pct": _q((mv - pc) / pc * 100) if pc else None,
            "realized_pnl": _q(t["realized_pnl"]),
            "cash": _q(cash.get(cur, ZERO)),
        }

    total_mv = sum((r["market_value"] for r in rows if r["market_value"] is not None), ZERO)
    positions = []
    for row in sorted(rows, key=lambda x: x["market_value"] or ZERO, reverse=True):
        mv = row["market_value"]
        positions.append(
            {
                "ticker": row["ticker"],
                "currency": row["currency"],
                "quantity": format(row["quantity"].normalize(), "f"),
                "average_cost": _q(row["average_cost"]),
                "cost_basis": _q(row["cost_basis"]),
                "market_price": _q(row["market_price"]),
                "market_value": _q(mv),
                "unrealized_pnl": _q(row["unrealized_pnl"]),
                "unrealized_pct": _q(row["unrealized_pct"]),
                "weight_pct": _q(mv / total_mv * 100) if mv is not None and total_mv else None,
                "realized_pnl": _q(row["realized_pnl"]),
            }
        )

    latest = max(snaps.values(), key=lambda s: s.as_of, default=None)
    return {
        "prices": {
            "source": "Reporte de GBM (no es tiempo real)",
            "as_of": latest.as_of if latest else None,
            "age_hours": round((now - latest.as_of).total_seconds() / 3600, 1) if latest else None,
        },
        "totals_by_currency": out_totals,
        "positions": positions,
        "unpriced_positions": sum(1 for r in rows if r["market_value"] is None),
        "disclaimer": (
            "Resultados contables en moneda original. Costos de apertura aproximados (promedio de GBM "
            "a centavos). No es ganancia fiscal ni asesoría de inversión."
        ),
    }


class DashboardView(APIView):
    def get(self, request):
        return Response(build_dashboard(request.user))
