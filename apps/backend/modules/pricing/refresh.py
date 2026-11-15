"""Actualización de precios y tipo de cambio con cadena de respaldo.

Los proveedores se prueban en orden de preferencia. Si uno está caído o limitó el uso (error transitorio),
se pasa al siguiente y, tras dos fallos seguidos (o un límite de uso), se deja de insistir en él durante el
resto de la corrida (cortacircuito) para no gastar tiempo ni cuota. Un símbolo inexistente NO abre el
cortacircuito: solo se prueba el respaldo para ese símbolo. Cada precio se guarda con el proveedor que
realmente respondió."""

from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.models import TaxLot

from .providers import ProviderError
from .services import FALLBACK_PROVIDERS, PricingError, record_fx, record_price

BREAKER_STRIKES = 2


def held_tickers() -> dict[str, Instrument]:
    """Emisoras con posición abierta (solo se consultan las que importan) -> instrumento."""
    ids = set(TaxLot.objects.filter(quantity_remaining__gt=0).values_list("instrument_id", flat=True))
    out = {}
    for ident in InstrumentIdentifier.objects.filter(kind="TICKER", instrument_id__in=ids).select_related(
        "instrument"
    ):
        out[ident.value.upper()] = ident.instrument
    return out


def _as_list(providers) -> list:
    return list(providers) if isinstance(providers, list | tuple) else [providers]


def refresh_prices(providers) -> dict:
    """`providers`: un proveedor o una lista en orden de preferencia (el primero que responde gana)."""
    chain = _as_list(providers)
    result = {"updated": [], "unchanged": [], "failed": {}, "sources": {}, "fallback_used": []}
    strikes = dict.fromkeys(range(len(chain)), 0)
    open_breakers: set[int] = set()
    for ticker, instrument in sorted(held_tickers().items()):
        if len(open_breakers) == len(chain):
            result["failed"]["_abortado"] = "Todos los proveedores quedaron fuera de servicio; el resto no se consultó."
            break
        errors, done = [], False
        for idx, provider in enumerate(chain):
            if idx in open_breakers:
                continue
            try:
                quote = provider.fetch_quote(ticker)
                if quote.currency != instrument.currency:
                    raise ProviderError(f"moneda {quote.currency} distinta de {instrument.currency}")
                _, created = record_price(instrument, quote.price, provider.name, quote.observed_at)
            except (ProviderError, PricingError) as exc:
                errors.append(f"{provider.name}: {exc}")
                if isinstance(exc, ProviderError) and exc.transient:
                    strikes[idx] += 1
                    if strikes[idx] >= BREAKER_STRIKES or "Límite de consultas" in str(exc):
                        open_breakers.add(idx)
                continue
            strikes[idx] = 0
            (result["updated"] if created else result["unchanged"]).append(ticker)
            result["sources"][ticker] = provider.name
            if idx > 0 or provider.name in FALLBACK_PROVIDERS:  # no oficial, aunque sea el único disponible
                result["fallback_used"].append(ticker)
            done = True
            break
        if not done:
            result["failed"][ticker] = " | ".join(errors) or "sin proveedores disponibles"
    return result


def refresh_fx(providers) -> dict:
    """USD/MXN con respaldo: el primer proveedor que responde gana; se informa cuál y si fue respaldo."""
    chain, errors = _as_list(providers), []
    for idx, provider in enumerate(chain):
        try:
            rate, observed_at = provider.fetch_fx()
            fx, created = record_fx("USD", "MXN", rate, provider.name, observed_at)
        except (ProviderError, PricingError) as exc:
            errors.append(f"{provider.name}: {exc}")
            continue
        return {
            "ok": True,
            "created": created,
            "rate": str(fx.rate.normalize()),
            "observed_at": fx.observed_at,
            "source": provider.name,
            "fallback": idx > 0 or provider.name in FALLBACK_PROVIDERS,
        }
    return {"ok": False, "error": " | ".join(errors) or "sin proveedores disponibles"}
