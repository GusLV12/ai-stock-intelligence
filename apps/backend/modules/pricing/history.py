"""Descarga e idempotencia del historial de cierres diarios de las emisoras con posición."""

from .history_models import DailyClose
from .providers import ProviderError
from .refresh import held_tickers

CONSECUTIVE_FAILURES = 2


def refresh_history(provider, range_: str = "1y", tickers: dict | None = None) -> dict:
    """`tickers` ({ticker: instrumento}) sustituye a las emisoras con posición (p. ej. el universo de investigación).
    Guarda cierres nuevos (nunca duplica ni sobrescribe el cierre real). Un fallo individual no detiene el resto; dos fallos
    transitorios seguidos (proveedor caído) abortan para no seguir insistiendo."""
    result = {"saved": {}, "unchanged": [], "failed": {}}
    strikes = 0
    for ticker, instrument in sorted((held_tickers() if tickers is None else tickers).items()):
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
        existing = {
            r.date: r for r in DailyClose.objects.filter(instrument=instrument, provider=provider.name)
        }
        full = {b["date"]: b for b in data.get("ohlcv", [])}
        new, fill = [], []
        for day, close in data["bars"]:
            bar = full.get(day, {})
            row = existing.get(day)
            if row is None:
                new.append(
                    DailyClose(
                        instrument=instrument,
                        date=day,
                        close=close,
                        currency=data["currency"],
                        provider=provider.name,
                        open=bar.get("open"),
                        high=bar.get("high"),
                        low=bar.get("low"),
                        volume=bar.get("volume"),
                        adj_close=bar.get("adj_close"),
                    )
                )
                continue
            touched = False
            if row.high is None and bar.get("high") is not None:
                # Cierre ya guardado sin vela: se completa open/high/low/volume sin tocar el cierre.
                row.open, row.high, row.low, row.volume = bar["open"], bar["high"], bar["low"], bar["volume"]
                touched = True
            if bar.get("adj_close") is not None and row.adj_close != bar["adj_close"]:
                row.adj_close = bar[
                    "adj_close"
                ]  # el ajuste por dividendos se corrige hacia atrás con cada pago nuevo
                touched = True
            if touched:
                fill.append(row)
        DailyClose.objects.bulk_create(new)
        if fill:
            DailyClose.objects.bulk_update(fill, ["open", "high", "low", "volume", "adj_close"])
        if new or fill:
            result["saved"][ticker] = len(new) + len(fill)
        else:
            result["unchanged"].append(ticker)
    return result
