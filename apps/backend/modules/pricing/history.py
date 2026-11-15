"""Descarga e idempotencia del historial de cierres diarios de las emisoras con posición."""

from .history_models import DailyClose
from .providers import ProviderError
from .refresh import held_tickers

CONSECUTIVE_FAILURES = 2


def refresh_history(provider, range_: str = "1y") -> dict:
    """Guarda cierres nuevos (nunca duplica ni sobrescribe). Un fallo individual no detiene el resto; dos fallos
    transitorios seguidos (proveedor caído) abortan para no seguir insistiendo."""
    result = {"saved": {}, "unchanged": [], "failed": {}}
    strikes = 0
    for ticker, instrument in sorted(held_tickers().items()):
        try:
            data = provider.fetch_history(ticker, range_)
            if data["currency"] != instrument.currency:
                raise ProviderError(f"moneda {data['currency']} distinta de {instrument.currency}")
        except ProviderError as exc:
            result["failed"][ticker] = str(exc)
            strikes = strikes + 1 if exc.transient else 0
            if strikes >= CONSECUTIVE_FAILURES:
                result["failed"]["_abortado"] = "El proveedor no responde; el resto no se consultó."
                break
            continue
        strikes = 0
        existing = set(DailyClose.objects.filter(instrument=instrument, provider=provider.name).values_list("date", flat=True))
        new = [
            DailyClose(instrument=instrument, date=day, close=close, currency=data["currency"], provider=provider.name)
            for day, close in data["bars"]
            if day not in existing
        ]
        DailyClose.objects.bulk_create(new)
        if new:
            result["saved"][ticker] = len(new)
        else:
            result["unchanged"].append(ticker)
    return result
