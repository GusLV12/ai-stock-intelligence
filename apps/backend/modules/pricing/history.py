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
        existing = {r.date: r for r in DailyClose.objects.filter(instrument=instrument, provider=provider.name)}
        full = {b["date"]: b for b in data.get("ohlcv", [])}
        new, fill = [], []
        for day, close in data["bars"]:
            bar = full.get(day, {})
            row = existing.get(day)
            if row is None:
                new.append(
                    DailyClose(
                        instrument=instrument, date=day, close=close, currency=data["currency"], provider=provider.name,
                        open=bar.get("open"), high=bar.get("high"), low=bar.get("low"), volume=bar.get("volume"),
                    )
                )
            elif row.high is None and bar.get("high") is not None:
                # Cierre ya guardado sin vela: se completa open/high/low/volume sin tocar el cierre.
                row.open, row.high, row.low, row.volume = bar["open"], bar["high"], bar["low"], bar["volume"]
                fill.append(row)
        DailyClose.objects.bulk_create(new)
        if fill:
            DailyClose.objects.bulk_update(fill, ["open", "high", "low", "volume"])
        if new or fill:
            result["saved"][ticker] = len(new) + len(fill)
        else:
            result["unchanged"].append(ticker)
    return result
