"""Datos del dashboard. Cantidades y costos salen del libro; el valor es títulos × último precio
observado (con su proveedor y antigüedad). Sin precio, la posición queda "sin precio": nunca se
inventa un valor ni se mezcla en los totales de mercado."""

from collections import defaultdict
from decimal import Decimal

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.accounts.models import InvestmentAccount
from modules.imports.models import PositionSnapshot
from modules.instruments.models import InstrumentIdentifier
from modules.pricing.services import FALLBACK_PROVIDERS, latest_fx, latest_price

from .api import _positions_for
from .performance import money_weighted_return

ZERO = Decimal(0)
CENT = Decimal("0.01")


def _q(v: Decimal | None, places=CENT):
    return None if v is None else str(v.quantize(places))


def _age_hours(now, moment):
    return round((now - moment).total_seconds() / 3600, 1)


def build_dashboard(user, account_ids=None) -> dict:
    """account_ids (opcional) limita el resultado a esas cuentas (p. ej. las que autoriza una credencial MCP)."""
    now = timezone.now()
    tickers = {i.instrument_id: i.value for i in InstrumentIdentifier.objects.filter(kind="TICKER")}

    rows, observed = [], []
    for r in _positions_for(user, account_ids=account_ids):
        mp = latest_price(_instrument(r["instrument"]))
        cost = r["cost_basis"]
        price = mp.price if mp else None
        value = (r["quantity"] * price) if price is not None else None
        if mp:
            observed.append(mp)
        rows.append(
            {
                "ticker": tickers.get(r["instrument"], r["instrument_name"]),
                "currency": r["currency"],
                "quantity": r["quantity"],
                "average_cost": r["average_cost"],
                "cost_basis": cost,
                "market_price": price,
                "market_value": value,
                "price_provider": mp.provider if mp else None,
                "price_observed_at": mp.observed_at if mp else None,
                "price_age_hours": _age_hours(now, mp.observed_at) if mp else None,
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
            t["priced_count"] += 1

    cash = defaultdict(lambda: ZERO)
    cash_dates = defaultdict(list)
    latest_snap = {}
    snap_qs = PositionSnapshot.objects.filter(account__owner=user)
    if account_ids is not None:
        snap_qs = snap_qs.filter(account_id__in=list(account_ids))
    for s in snap_qs.order_by("as_of"):
        latest_snap[s.account_id] = s
    for s in latest_snap.values():
        if s.cash_amount is not None:
            cash[s.cash_currency] += s.cash_amount
            cash_dates[s.cash_currency].append(s.as_of)

    acc_qs = InvestmentAccount.objects.filter(owner=user)
    if account_ids is not None:
        acc_qs = acc_qs.filter(id__in=list(account_ids))
    accounts = list(acc_qs)
    out_totals = {}
    for cur, t in totals.items():
        mv, pc = t["market_value"], t["priced_cost_basis"]
        # La moneda base no limita las monedas que puede tener una cuenta. Sin un
        # reporte de cada cuenta no se puede afirmar que el efectivo esté completo.
        cash_accounts = accounts
        cash_complete = bool(cash_accounts) and all(
            a.id in latest_snap
            and latest_snap[a.id].cash_amount is not None
            and latest_snap[a.id].cash_currency == cur
            for a in cash_accounts
        )
        out_totals[cur] = {
            "cost_basis": _q(t["cost_basis"]),
            "market_value": _q(mv) if t["priced_count"] else None,
            "unrealized_pnl": _q(mv - pc) if t["priced_count"] else None,
            "unrealized_pct": _q((mv - pc) / pc * 100) if pc else None,
            "realized_pnl": _q(t["realized_pnl"]),
            "cash": _q(cash[cur]) if cur in cash else None,
            "cash_observed_at": min(cash_dates[cur]) if cash_dates[cur] else None,
            "cash_complete": cash_complete,
            "cash_note": "Efectivo de los reportes disponibles; no refleja movimientos posteriores.",
        }

    fx_block = None
    if "USD" in out_totals and (fx := latest_fx("USD", "MXN")):
        cash_usd = out_totals["USD"]["cash"]
        market_usd = out_totals["USD"]["market_value"]
        mv = (
            Decimal(market_usd) + Decimal(cash_usd)
            if cash_usd is not None and market_usd is not None and out_totals["USD"]["cash_complete"]
            else None
        )
        fx_block = {
            "pair": "USD/MXN",
            "rate": str(fx.rate.normalize()),
            "provider": fx.provider,
            "observed_at": fx.observed_at,
            "age_hours": _age_hours(now, fx.observed_at),
            "total_value_mxn": _q(mv * fx.rate) if mv is not None else None,
            "is_fallback": fx.provider in FALLBACK_PROVIDERS,
            "note": "Tipo de cambio de referencia; no es el que aplica el broker.",
        }

    perf_account_ids = [a.id for a in accounts]
    newest = max((p.observed_at for p in observed), default=None)
    performance = {}
    for cur in out_totals:
        in_cur = [r for r in rows if r["currency"] == cur]
        priced_all = all(r["market_value"] is not None for r in in_cur)
        terminal = totals[cur]["market_value"] if priced_all else None
        performance[cur] = money_weighted_return(perf_account_ids, cur, terminal, newest or now, priced_all)

    # Nunca sumar cantidades expresadas en monedas diferentes para ponderar.
    total_mv = {cur: t["market_value"] for cur, t in totals.items()}
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
                "price_provider": row["price_provider"],
                "price_observed_at": row["price_observed_at"],
                "price_age_hours": row["price_age_hours"],
                "unrealized_pnl": _q(row["unrealized_pnl"]),
                "unrealized_pct": _q(row["unrealized_pct"]),
                "weight_pct": _q(mv / total_mv[row["currency"]] * 100)
                if mv is not None and total_mv[row["currency"]]
                else None,
                "realized_pnl": _q(row["realized_pnl"]),
            }
        )

    prices = {
        "providers": sorted({p.provider for p in observed}),
        "uses_fallback": any(p.provider in FALLBACK_PROVIDERS for p in observed),
        "newest_at": max((p.observed_at for p in observed), default=None),
        "oldest_at": min((p.observed_at for p in observed), default=None),
        "oldest_age_hours": _age_hours(now, min(p.observed_at for p in observed)) if observed else None,
        "note": "Precios de reportes u observaciones registradas; no son tiempo real.",
    }
    return {
        "prices": prices,
        "fx": fx_block,
        "performance": performance,
        "totals_by_currency": out_totals,
        "positions": positions,
        "unpriced_positions": sum(1 for r in rows if r["market_value"] is None),
        "disclaimer": (
            "Resultados contables en moneda original. Costos de apertura aproximados (promedio de GBM "
            "a centavos). No es ganancia fiscal ni asesoría de inversión."
        ),
    }


def _instrument(pk):
    from modules.instruments.models import Instrument

    return Instrument.objects.get(pk=pk)


class DashboardView(APIView):
    def get(self, request):
        return Response(build_dashboard(request.user))
