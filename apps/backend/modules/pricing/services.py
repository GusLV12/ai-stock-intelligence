from datetime import datetime
from decimal import Decimal

from modules.instruments.models import Instrument

from .models import FxRate, MarketPrice

# Fuentes de respaldo no oficiales: se muestran con aviso porque pueden retrasarse o fallar sin aviso.
FALLBACK_PROVIDERS = {"YAHOO_UNOFFICIAL"}


class PricingError(ValueError):
    pass


def record_price(
    instrument: Instrument, price: Decimal, provider: str, observed_at: datetime, currency: str | None = None
) -> tuple[MarketPrice, bool]:
    """Idempotente: la misma observación (instrumento, proveedor, momento) no se duplica."""
    currency = currency or instrument.currency
    if price < 0:
        raise PricingError("El precio no puede ser negativo.")
    if observed_at.tzinfo is None:
        raise PricingError("La fecha de observación debe incluir zona horaria.")
    if currency != instrument.currency:
        raise PricingError("La moneda del precio no coincide con la del instrumento.")
    return MarketPrice.objects.get_or_create(
        instrument=instrument, provider=provider, observed_at=observed_at,
        defaults={"price": price, "currency": currency},
    )  # fmt: skip


def latest_price(instrument: Instrument) -> MarketPrice | None:
    return MarketPrice.objects.filter(instrument=instrument).first()  # orden: más reciente primero


def record_fx(base: str, quote: str, rate: Decimal, provider: str, observed_at: datetime) -> tuple[FxRate, bool]:
    if rate <= 0:
        raise PricingError("El tipo de cambio debe ser positivo.")
    if observed_at.tzinfo is None:
        raise PricingError("La fecha de observación debe incluir zona horaria.")
    base, quote = base.upper(), quote.upper()
    if base == quote:
        raise PricingError("Las monedas deben ser distintas.")
    return FxRate.objects.get_or_create(
        base=base, quote=quote, provider=provider, observed_at=observed_at, defaults={"rate": rate}
    )


def latest_fx(base: str, quote: str) -> FxRate | None:
    return FxRate.objects.filter(base=base.upper(), quote=quote.upper()).first()
