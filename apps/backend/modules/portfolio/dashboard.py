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


def report_dashboard(user):
    """Vista de una fotografía GBM, sin modificar ni completar el libro contable."""
    accounts = list(InvestmentAccount.objects.filter(owner=user))
    if len(accounts) != 1 or accounts[0].broker != InvestmentAccount.Broker.GBM_USA:
        return None
    snapshots = PositionSnapshot.objects.filter(account=accounts[0]).order_by("-as_of", "-id")
    snapshot = snapshots.first()
    if snapshot is None:
        return None
    # En caso de fechas iguales no se puede afirmar qué reporte es posterior.
    if snapshots.filter(as_of=snapshot.as_of).count() != 1:
        return None
    previous = snapshots.filter(as_of__lt=snapshot.as_of).first()
    lines = list(snapshot.lines.all())
    before = {line.ticker: line.quantity for line in previous.lines.all()} if previous else {}
    after = {line.ticker: line.quantity for line in lines}
    changes = []
    if previous:
        for ticker in sorted(before.keys() | after.keys()):
            old, new = before.get(ticker, ZERO), after.get(ticker, ZERO)
            if old != new:
                changes.append(
                    {
                        "ticker": ticker,
                        "previous": str(old),
                        "current": str(new),
                        "kind": "ausente" if new == 0 else "disminucion" if new < old else "aumento",
                    }
                )

    active = [line for line in lines if line.quantity > 0]
    costs_complete = all(line.average_cost is not None for line in active)
    prices_complete = all(line.market_price is not None for line in active)
    total_value = sum(
        (line.quantity * line.market_price for line in active if line.market_price is not None), ZERO
    )
    total_cost = sum(
        (line.quantity * line.average_cost for line in active if line.average_cost is not None), ZERO
    )
    rows = []
    for line in active:
        value = line.quantity * line.market_price if line.market_price is not None else None
        cost = line.quantity * line.average_cost if line.average_cost is not None else None
        profit = value - cost if value is not None and cost is not None else None
        rows.append(
            {
                "ticker": line.ticker,
                "currency": snapshot.cash_currency,
                "quantity": format(line.quantity.normalize(), "f"),
                "average_cost": _q(line.average_cost),
                "cost_basis": _q(cost),
                "market_price": _q(line.market_price),
                "market_value": _q(value),
                "price_provider": "GBM_REPORT" if line.market_price is not None else None,
                "price_observed_at": snapshot.as_of if line.market_price is not None else None,
                "price_age_hours": _age_hours(timezone.now(), snapshot.as_of)
                if line.market_price is not None
                else None,
                "unrealized_pnl": _q(profit),
                "unrealized_pct": _q(profit / cost * 100) if profit is not None and cost else None,
                "weight_pct": _q(value / total_value * 100)
                if value is not None and total_value and prices_complete
                else None,
                "realized_pnl": None,
            }
        )
    rows.sort(key=lambda row: Decimal(row["market_value"] or "0"), reverse=True)
    cur = snapshot.cash_currency
    pnl = total_value - total_cost if costs_complete and prices_complete else None
    totals = {
        cur: {
            "cost_basis": _q(total_cost) if costs_complete else None,
            "market_value": _q(total_value) if prices_complete else None,
            "unrealized_pnl": _q(pnl),
            "unrealized_pct": _q(pnl / total_cost * 100) if pnl is not None and total_cost else None,
            "realized_pnl": None,
            "cash": _q(snapshot.cash_amount),
            "cash_observed_at": snapshot.as_of if snapshot.cash_amount is not None else None,
            "cash_complete": snapshot.cash_amount is not None,
        }
    }
    fx = None
    if cur == "USD" and (rate := latest_fx("USD", "MXN")):
        total_mxn = (
            (total_value + snapshot.cash_amount) * rate.rate
            if prices_complete and snapshot.cash_amount is not None
            else None
        )
        fx = {
            "pair": "USD/MXN",
            "rate": str(rate.rate.normalize()),
            "provider": rate.provider,
            "observed_at": rate.observed_at,
            "is_fallback": rate.provider in FALLBACK_PROVIDERS,
            "total_value_mxn": _q(total_mxn),
        }
    has_prices = any(line.market_price is not None for line in active)
    return {
        "prices": {
            "providers": ["GBM_REPORT"] if has_prices else [],
            "uses_fallback": False,
            "newest_at": snapshot.as_of if has_prices else None,
            "oldest_at": snapshot.as_of if has_prices else None,
            "oldest_age_hours": _age_hours(timezone.now(), snapshot.as_of) if has_prices else None,
        },
        "fx": fx,
        "performance": {
            cur: {
                "xirr_pct": None,
                "reason": "No evaluable con una fotografía de posiciones: faltan los movimientos conciliados.",
            }
        },
        "totals_by_currency": totals,
        "positions": rows,
        "unpriced_positions": sum(line.market_price is None for line in active),
        "report": {
            "as_of": snapshot.as_of,
            "previous_as_of": previous.as_of if previous else None,
            "changes": changes,
        },
        "disclaimer": "Posiciones, precios y costos aproximados del reporte GBM; no incluyen movimientos posteriores. Las variaciones no prueban ventas ni resultados realizados.",
    }


class DashboardView(APIView):
    def get(self, request):
        if request.query_params.get("source") == "report":
            data = report_dashboard(request.user)
            if data is not None:
                return Response(data)
        return Response(build_dashboard(request.user))
