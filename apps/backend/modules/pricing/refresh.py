"""Actualización de precios y tipo de cambio desde proveedores externos."""

from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.models import TaxLot

from .providers import BanxicoFixProvider, ProviderError, TwelveDataProvider
from .services import PricingError, record_fx, record_price


def held_tickers() -> dict[str, Instrument]:
    """Emisoras con posición abierta (solo se consultan las que importan) -> instrumento."""
    ids = set(TaxLot.objects.filter(quantity_remaining__gt=0).values_list("instrument_id", flat=True))
    out = {}
    for ident in InstrumentIdentifier.objects.filter(kind="TICKER", instrument_id__in=ids).select_related(
        "instrument"
    ):
        out[ident.value.upper()] = ident.instrument
    return out


def refresh_prices(provider: TwelveDataProvider) -> dict:
    """Consulta cada emisora; un fallo individual no detiene el resto."""
    result = {"updated": [], "unchanged": [], "failed": {}}
    for ticker, instrument in sorted(held_tickers().items()):
        try:
            quote = provider.fetch_quote(ticker)
            if quote.currency != instrument.currency:
                raise ProviderError(f"moneda {quote.currency} distinta de {instrument.currency}")
            _, created = record_price(instrument, quote.price, provider.name, quote.observed_at)
        except (ProviderError, PricingError) as exc:
            result["failed"][ticker] = str(exc)
            if "Límite de consultas" in str(exc):  # seguir consultando solo gastaría más cuota
                result["failed"]["_abortado"] = "Se alcanzó el límite del proveedor; el resto no se consultó."
                break
            continue
        (result["updated"] if created else result["unchanged"]).append(ticker)
    return result


def refresh_fx(provider: BanxicoFixProvider) -> dict:
    try:
        rate, observed_at = provider.fetch_fix()
        fx, created = record_fx("USD", "MXN", rate, provider.name, observed_at)
    except (ProviderError, PricingError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "created": created, "rate": str(fx.rate.normalize()), "observed_at": fx.observed_at}
