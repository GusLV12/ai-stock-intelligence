"""Adaptadores de proveedores externos de datos de mercado.

- Twelve Data (precios de acciones; plan gratuito con retraso y límite por minuto).
- Banxico SIE (tipo de cambio FIX USD/MXN; token gratuito).

Reglas: solo se envían tickers (nunca datos de la cuenta); las claves no se registran ni aparecen
en mensajes de error; la hora guardada es la que reporta el proveedor, no la del momento de la consulta.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

MAX_BYTES = 1_000_000
MX = ZoneInfo("America/Mexico_City")


class ProviderError(Exception):
    """Fallo de un proveedor. El mensaje nunca incluye URLs ni claves."""


def http_get_json(url: str, headers: dict | None = None, timeout: int = 15) -> dict:
    if not url.startswith("https://"):
        raise ProviderError("Solo se permiten conexiones HTTPS.")
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - esquema https validado arriba
            raw = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"El proveedor respondió HTTP {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError("No se pudo conectar con el proveedor.") from None
    if len(raw) > MAX_BYTES:
        raise ProviderError("Respuesta demasiado grande.")
    try:
        data = json.loads(raw)
    except ValueError:
        raise ProviderError("Respuesta del proveedor no es JSON válido.") from None
    if not isinstance(data, dict):
        raise ProviderError("Respuesta inesperada del proveedor.")
    return data


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    currency: str
    observed_at: datetime
    market_open: bool


def _decimal(value, what: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ProviderError(f"{what} inválido en la respuesta.") from None
    if not d.is_finite() or d < 0:
        raise ProviderError(f"{what} inválido en la respuesta.")
    return d


class TwelveDataProvider:
    name = "TWELVE_DATA"
    BASE = "https://api.twelvedata.com/quote"

    def __init__(self, api_key: str, per_minute: int = 8, http=http_get_json, sleep=time.sleep, clock=time.monotonic):
        if not api_key:
            raise ProviderError("Falta TWELVE_DATA_API_KEY.")
        self._key, self._http, self._sleep, self._clock = api_key, http, sleep, clock
        self._interval = 60.0 / max(per_minute, 1)
        self._last = None

    def _throttle(self):
        now = self._clock()
        if self._last is not None and now - self._last < self._interval:
            self._sleep(self._interval - (now - self._last))
        self._last = self._clock()

    def fetch_quote(self, symbol: str) -> Quote:
        self._throttle()
        url = f"{self.BASE}?{urllib.parse.urlencode({'symbol': symbol, 'apikey': self._key})}"
        data = self._http(url)
        if data.get("status") == "error" or "code" in data:
            code = data.get("code", "?")
            if code == 429:
                raise ProviderError("Límite de consultas del proveedor alcanzado.")
            raise ProviderError(f"El proveedor rechazó {symbol} (código {code}).")
        price = _decimal(data.get("close"), "Precio")
        # Hora real de la última cotización; si falta, la del periodo. Nunca la hora de la consulta.
        ts = data.get("last_quote_at") or data.get("timestamp")
        try:
            observed = datetime.fromtimestamp(int(ts), UTC)
        except (TypeError, ValueError, OverflowError, OSError):
            raise ProviderError(f"Hora de cotización inválida para {symbol}.") from None
        currency = str(data.get("currency") or "").upper()
        if len(currency) != 3:
            raise ProviderError(f"Falta la moneda de {symbol}.")
        return Quote(symbol, price, currency, observed, bool(data.get("is_market_open")))


class BanxicoFixProvider:
    name = "BANXICO_FIX"
    SERIES = "SF43718"  # tipo de cambio FIX, pesos por dólar (fecha de determinación)
    URL = f"https://www.banxico.org.mx/SieAPIRest/service/v1/series/{SERIES}/datos/oportuno"

    def __init__(self, token: str, http=http_get_json):
        if not token:
            raise ProviderError("Falta BANXICO_TOKEN.")
        self._token, self._http = token, http

    def fetch_fix(self) -> tuple[Decimal, datetime]:
        data = self._http(self.URL, headers={"Bmx-Token": self._token, "Accept": "application/json"})
        try:
            datos = data["bmx"]["series"][0]["datos"][0]
            raw_rate, raw_date = datos["dato"], datos["fecha"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError("Respuesta de Banxico con formato inesperado.") from None
        if str(raw_rate).strip().upper() == "N/E":
            raise ProviderError("Banxico aún no publica el dato.")
        rate = _decimal(str(raw_rate).replace(",", ""), "Tipo de cambio")
        if rate <= 0:
            raise ProviderError("Tipo de cambio inválido en la respuesta.")
        try:
            day = datetime.strptime(raw_date, "%d/%m/%Y").date()
        except ValueError:
            raise ProviderError("Fecha inválida en la respuesta de Banxico.") from None
        # El FIX se determina hacia el mediodía (hora de México) del día de la fecha.
        return rate, datetime(day.year, day.month, day.day, 12, 0, tzinfo=MX)
