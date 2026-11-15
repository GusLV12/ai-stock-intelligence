"""Posiciones valoradas del usuario como base común de exposición, rebalanceo y shocks.

Valor = títulos del libro × último precio observado (con su proveedor y antigüedad). Una posición sin precio
NO se valora ni se mezcla en los pesos: se lista aparte. El efectivo reportado por el broker entra como
clase CASH. Las posiciones en MXN se convierten a USD solo si hay un tipo de cambio de referencia."""

from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone

from modules.accounts.models import InvestmentAccount
from modules.imports.models import PositionSnapshot
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.api import _positions_for
from modules.pricing.services import latest_fx, latest_price

ZERO = Decimal(0)
BASE_CURRENCY = "USD"


@dataclass
class Holding:
    ticker: str
    name: str
    instrument_id: int | None
    asset_class: str
    sector_kind: str
    currency: str
    account: str
    broker: str
    quantity: Decimal
    price: Decimal | None
    value: Decimal | None  # en la moneda original
    value_base: Decimal | None  # en USD (None si no se pudo convertir o no hay precio)
    price_provider: str | None
    price_age_hours: float | None
    quantity_scale: int = 6


def _to_base(value: Decimal, currency: str, fx_usd_mxn: Decimal | None) -> Decimal | None:
    if currency == BASE_CURRENCY:
        return value
    if currency == "MXN" and fx_usd_mxn:
        return value / fx_usd_mxn
    return None


def load_holdings(user) -> dict:
    now = timezone.now()
    fx = latest_fx("USD", "MXN")
    fx_rate = fx.rate if fx else None
    tickers = {i.instrument_id: i.value for i in InstrumentIdentifier.objects.filter(kind="TICKER")}
    instruments = {i.id: i for i in Instrument.objects.all()}
    accounts = {a.id: a for a in InvestmentAccount.objects.filter(owner=user)}

    holdings, unpriced, unconverted = [], [], []
    for r in _positions_for(user):
        ins = instruments[r["instrument"]]
        acc = accounts[r["account"]]
        mp = latest_price(ins)
        price = mp.price if mp else None
        value = r["quantity"] * price if price is not None else None
        base = _to_base(value, r["currency"], fx_rate) if value is not None else None
        h = Holding(
            ticker=tickers.get(ins.id, ins.name), name=ins.name, instrument_id=ins.id, asset_class=ins.asset_class,
            sector_kind=ins.sector_kind, currency=r["currency"], account=acc.name, broker=acc.broker,
            quantity=r["quantity"], price=price, value=value, value_base=base,
            price_provider=mp.provider if mp else None,
            price_age_hours=round((now - mp.observed_at).total_seconds() / 3600, 1) if mp else None,
            quantity_scale=ins.quantity_scale,
        )  # fmt: skip
        holdings.append(h)
        if value is None:
            unpriced.append(h.ticker)
        elif base is None:
            unconverted.append(h.ticker)

    # Efectivo del último reporte de cada cuenta (solo USD se incorpora a la base).
    latest = {}
    for s in PositionSnapshot.objects.filter(account__owner=user).order_by("as_of"):
        latest[s.account_id] = s
    cash = []
    for s in latest.values():
        if s.cash_amount and s.cash_amount > 0:
            base = _to_base(s.cash_amount, s.cash_currency, fx_rate)
            h = Holding("CASH", f"Efectivo ({accounts[s.account_id].name})", None, "CASH", "non_financial", s.cash_currency,
                        accounts[s.account_id].name, accounts[s.account_id].broker, s.cash_amount, Decimal(1),
                        s.cash_amount, base, "REPORTE", None)  # fmt: skip
            cash.append(h)
    return {
        "holdings": [*holdings, *cash],
        "unpriced": unpriced,
        "unconverted": unconverted,
        "fx": {"pair": "USD/MXN", "rate": fx_rate, "provider": fx.provider if fx else None} if fx else None,
    }
