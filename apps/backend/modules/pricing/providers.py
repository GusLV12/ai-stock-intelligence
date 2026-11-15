"""Adaptadores de proveedores externos de datos de mercado.

- Twelve Data (precios de acciones; plan gratuito con retraso y límite por minuto).
- Banxico SIE (tipo de cambio FIX USD/MXN; token gratuito).
- Yahoo Finance (RESPALDO, no oficial: sin clave, puede retrasarse, cambiar o bloquearse sin aviso).

Reglas: solo se envían tickers (nunca datos de la cuenta); las claves no se registran ni aparecen
en mensajes de error; la hora guardada es la que reporta el proveedor, no la del momento de la consulta.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

MAX_BYTES = 1_000_000
MX = ZoneInfo("America/Mexico_City")


class ProviderError(Exception):
    """Fallo de un proveedor. El mensaje nunca incluye URLs ni claves.

    transient=True: el proveedor está caído, saturado o limitó el uso (conviene probar el respaldo y
    dejar de insistir). transient=False: el dato pedido es inválido (p. ej. símbolo inexistente)."""

    def __init__(self, message: str = "", transient: bool = False):
        super().__init__(message)
        self.transient = transient


def http_get_json(url: str, headers: dict | None = None, timeout: int = 15) -> dict:
    if not url.startswith("https://"):
        raise ProviderError("Solo se permiten conexiones HTTPS.")
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - esquema https validado arriba
            raw = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ProviderError(
            f"El proveedor respondió HTTP {exc.code}.", transient=exc.code == 429 or exc.code >= 500
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError("No se pudo conectar con el proveedor.", transient=True) from None
    if len(raw) > MAX_BYTES:
        raise ProviderError("Respuesta demasiado grande.", transient=True)
    try:
        data = json.loads(raw)
    except ValueError:
        raise ProviderError("Respuesta del proveedor no es JSON válido.", transient=True) from None
    if not isinstance(data, dict):
        raise ProviderError("Respuesta inesperada del proveedor.", transient=True)
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

    def __init__(
        self, api_key: str, per_minute: int = 8, http=http_get_json, sleep=time.sleep, clock=time.monotonic
    ):
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
                raise ProviderError("Límite de consultas del proveedor alcanzado.", transient=True)
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

    def fetch_fx(self) -> tuple[Decimal, datetime]:
        return self.fetch_fix()

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


class YahooProvider:
    """Respaldo no oficial (endpoint de gráficas de Yahoo Finance). Sin clave. Solo se envía el símbolo.
    Dos hosts (query2 y luego query1) ante fallos transitorios, pausa entre consultas y validación estricta."""

    name = "YAHOO_UNOFFICIAL"
    HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")
    USER_AGENT = "ai-stock-intelligence/1.0"  # genérico: sin datos personales
    FX_SYMBOL = "MXN=X"  # pesos por dólar
    MAX_FUTURE = 300  # segundos de tolerancia con relojes desfasados

    def __init__(
        self, http=http_get_json, sleep=time.sleep, clock=time.monotonic, pause: float = 0.3, now=None
    ):
        self._http, self._sleep, self._clock = http, sleep, clock
        self._pause, self._last = pause, None
        self._now = now or (lambda: datetime.now(UTC))

    def _throttle(self):
        now = self._clock()
        if self._last is not None and now - self._last < self._pause:
            self._sleep(self._pause - (now - self._last))
        self._last = self._clock()

    def _meta(self, symbol: str) -> dict:
        self._throttle()
        quoted = urllib.parse.quote(symbol, safe="")
        last = None
        for host in self.HOSTS:
            url = f"https://{host}/v8/finance/chart/{quoted}?range=1d&interval=1d"
            try:
                data = self._http(url, headers={"User-Agent": self.USER_AGENT, "Accept": "*/*"})
            except ProviderError as exc:
                if not exc.transient:
                    raise ProviderError(f"Yahoo no tiene datos de {symbol}.") from None
                last = exc
                continue
            chart = data.get("chart")
            if not isinstance(chart, dict):
                last = ProviderError("Respuesta de Yahoo con formato inesperado.", transient=True)
                continue
            if chart.get("error"):
                raise ProviderError(f"Yahoo no tiene datos de {symbol}.")
            try:
                return chart["result"][0]["meta"]
            except (KeyError, IndexError, TypeError):
                last = ProviderError("Respuesta de Yahoo con formato inesperado.", transient=True)
        raise last or ProviderError("Yahoo no disponible.", transient=True)

    def _price_and_time(self, symbol: str) -> tuple[Decimal, datetime, str]:
        meta = self._meta(symbol)
        price = _decimal(meta.get("regularMarketPrice"), "Precio")
        if price <= 0:
            raise ProviderError(f"Precio inválido de {symbol} en Yahoo.")
        try:
            observed = datetime.fromtimestamp(int(meta.get("regularMarketTime")), UTC)
        except (TypeError, ValueError, OverflowError, OSError):
            raise ProviderError(f"Hora de cotización inválida de {symbol} en Yahoo.") from None
        if (observed - self._now()).total_seconds() > self.MAX_FUTURE:
            raise ProviderError(f"Hora de cotización en el futuro para {symbol} en Yahoo.")
        return price, observed, str(meta.get("currency") or "").upper()

    def fetch_quote(self, symbol: str) -> Quote:
        # Yahoo usa guion en vez de punto (BRK.B -> BRK-B)
        price, observed, currency = self._price_and_time(symbol.replace(".", "-"))
        if len(currency) != 3:
            raise ProviderError(f"Falta la moneda de {symbol} en Yahoo.")
        return Quote(symbol, price, currency, observed, False)

    def fetch_fx(self) -> tuple[Decimal, datetime]:
        price, observed, currency = self._price_and_time(self.FX_SYMBOL)
        if currency != "MXN":
            raise ProviderError("Yahoo devolvió un par distinto de USD/MXN.")
        return price, observed

    # ---------- historial de cierres diarios ----------

    HISTORY_RANGES = {"1mo", "3mo", "6mo", "1y", "2y", "5y"}

    def fetch_history(self, symbol: str, range_: str = "1y") -> dict:
        """Cierres diarios: {'currency', 'bars': [(date, Decimal), ...]} en orden cronológico.
        Se descartan cierres nulos o no positivos, fechas repetidas y la barra de HOY (puede estar a medias)."""
        if range_ not in self.HISTORY_RANGES:
            raise ProviderError(f"Rango de historial no soportado: {range_}.")
        symbol = symbol.replace(".", "-")
        self._throttle()
        quoted = urllib.parse.quote(symbol, safe="")
        last = None
        for host in self.HOSTS:
            url = f"https://{host}/v8/finance/chart/{quoted}?range={range_}&interval=1d"
            try:
                data = self._http(url, headers={"User-Agent": self.USER_AGENT, "Accept": "*/*"})
            except ProviderError as exc:
                if not exc.transient:
                    raise ProviderError(f"Yahoo no tiene historial de {symbol}.") from None
                last = exc
                continue
            chart = data.get("chart")
            if not isinstance(chart, dict) or chart.get("error"):
                if isinstance(chart, dict) and chart.get("error"):
                    raise ProviderError(f"Yahoo no tiene historial de {symbol}.")
                last = ProviderError("Respuesta de Yahoo con formato inesperado.", transient=True)
                continue
            try:
                res = chart["result"][0]
                stamps = res["timestamp"]
                quote = res["indicators"]["quote"][0]
                closes = quote["close"]
                meta = res["meta"]
            except (KeyError, IndexError, TypeError):
                last = ProviderError("Respuesta de Yahoo con formato inesperado.", transient=True)
                continue
            try:  # cierre ajustado por dividendos (opcional: no todos los instrumentos lo traen)
                adj = res["indicators"]["adjclose"][0]["adjclose"]
            except (KeyError, IndexError, TypeError):
                adj = None
            return self._parse_bars(symbol, stamps, closes, meta, quote, adj)
        raise last or ProviderError("Yahoo no disponible.", transient=True)

    @staticmethod
    def _price(value):
        """Decimal de 4 decimales (sin ruido de float) o None si falta o no es positivo."""
        if value is None:
            return None
        try:
            d = _decimal(value, "Precio").quantize(Decimal("0.0001"))
        except (TypeError, ValueError, ProviderError):
            return None
        return d if d > 0 else None

    def _adj(self, adj, i: int, close: Decimal):
        """Cierre ajustado por dividendos. Debe ser positivo y no mayor que el cierre (un dividendo solo lo reduce);
        si no es coherente se descarta y esa barra queda sin ajuste."""
        if not isinstance(adj, list) or i >= len(adj):
            return None
        v = self._price(adj[i])
        return v if v is not None and v <= close * Decimal("1.001") else None

    def _ohlcv(self, quote: dict | None, i: int, close: Decimal) -> dict:
        """Velas completas solo si son coherentes (low <= open,close <= high); si no, solo el cierre."""
        empty = {"open": None, "high": None, "low": None, "volume": None}
        if not isinstance(quote, dict):
            return empty

        def at(key):
            seq = quote.get(key)
            return seq[i] if isinstance(seq, list) and i < len(seq) else None

        o, h, low = self._price(at("open")), self._price(at("high")), self._price(at("low"))
        if None in (o, h, low) or not (low <= min(o, close) and max(o, close) <= h):
            return empty
        vol = at("volume")
        try:
            volume = int(vol) if vol is not None and int(vol) >= 0 else None
        except (TypeError, ValueError):
            volume = None
        return {"open": o, "high": h, "low": low, "volume": volume}

    def _parse_bars(self, symbol, stamps, closes, meta, quote=None, adj=None) -> dict:
        currency = str(meta.get("currency") or "").upper()
        if len(currency) != 3:
            raise ProviderError(f"Falta la moneda de {symbol} en Yahoo.")
        offset = int(meta.get("gmtoffset") or 0)
        today_local = (self._now() + timedelta(seconds=offset)).date()
        if not isinstance(stamps, list) or not isinstance(closes, list):
            raise ProviderError("Respuesta de Yahoo con formato inesperado.", transient=True)
        by_date = {}
        full = {}
        for i, (ts, close) in enumerate(zip(stamps, closes, strict=False)):
            if close is None:
                continue
            try:
                day = (datetime.fromtimestamp(int(ts), UTC) + timedelta(seconds=offset)).date()
                value = _decimal(close, "Cierre").quantize(
                    Decimal("0.0001")
                )  # quita el ruido de float del proveedor
            except (TypeError, ValueError, OverflowError, OSError, ProviderError):
                continue
            if value <= 0 or day >= today_local:  # sin cierres inválidos ni la barra de hoy
                continue
            by_date[day] = value  # una barra por día; la posterior gana
            full[day] = {
                "date": day,
                "close": value,
                "adj_close": self._adj(adj, i, value),
                **self._ohlcv(quote, i, value),
            }
        if not by_date:
            raise ProviderError(f"Yahoo no devolvió cierres válidos para {symbol}.")
        return {
            "currency": currency,
            "bars": sorted(by_date.items()),
            "ohlcv": [full[d] for d in sorted(full)],
        }

    # ---------- velas intradía de 5 minutos (bajo demanda; no se guardan) ----------

    def fetch_intraday(self, symbol: str, range_: str = "1d") -> dict:
        """Velas crudas de 5 minutos: {'currency', 'timezone', 'raw': [{ts, open, high, low, close, volume}]}.
        La limpieza (horario regular, cuadrícula, velas completas) la hace modules.technical.intraday.clean."""
        if range_ not in {"1d", "5d"}:
            raise ProviderError(f"Rango intradía no soportado: {range_}.")
        symbol = symbol.replace(".", "-")
        self._throttle()
        quoted = urllib.parse.quote(symbol, safe="")
        last = None
        for host in self.HOSTS:
            url = f"https://{host}/v8/finance/chart/{quoted}?range={range_}&interval=5m"
            try:
                data = self._http(url, headers={"User-Agent": self.USER_AGENT, "Accept": "*/*"})
            except ProviderError as exc:
                if not exc.transient:
                    raise ProviderError(f"Yahoo no tiene datos intradía de {symbol}.") from None
                last = exc
                continue
            chart = data.get("chart")
            if isinstance(chart, dict) and chart.get("error"):
                raise ProviderError(f"Yahoo no tiene datos intradía de {symbol}.")
            try:
                res = chart["result"][0]
                stamps, q, meta = res["timestamp"], res["indicators"]["quote"][0], res["meta"]
            except (KeyError, IndexError, TypeError):
                last = ProviderError("Respuesta de Yahoo con formato inesperado.", transient=True)
                continue
            tz = str(meta.get("exchangeTimezoneName") or "")
            currency = str(meta.get("currency") or "").upper()
            if not tz or len(currency) != 3 or not isinstance(stamps, list):
                raise ProviderError(f"Faltan zona horaria o moneda de {symbol} en Yahoo.")
            raw = []
            for i, ts in enumerate(stamps):
                row = {"ts": ts}
                for key in ("open", "high", "low", "close", "volume"):
                    seq = q.get(key)
                    v = seq[i] if isinstance(seq, list) and i < len(seq) else None
                    row[key] = v if key == "volume" else self._price(v)
                raw.append(row)
            return {"currency": currency, "timezone": tz, "raw": raw}
        raise last or ProviderError("Yahoo no disponible.", transient=True)
