from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from modules.instruments.models import InstrumentIdentifier
from modules.pricing.providers import ProviderError, YahooProvider
from modules.valuations.fair_value import plain

from . import intraday

DELAY_NOTE = (
    "Velas de 5 minutos de una fuente gratuita no oficial (Yahoo Finance): pueden retrasarse, faltar o cambiar sin "
    "aviso. Verifica contra tu broker antes de actuar."
)


class IntradayThrottle(UserRateThrottle):
    scope = "intraday"


def get_provider() -> YahooProvider:
    """Punto único de construcción (las pruebas lo sustituyen por un doble)."""
    return YahooProvider()


def _freshness(clean, tz_name):
    now = datetime.now(UTC)
    if not clean["bars"]:
        return {"state": "sin_datos"}
    tz = ZoneInfo(tz_name)
    last_end = clean["bars"][-1]["start"].astimezone(UTC) + timedelta(minutes=intraday.BAR_MINUTES)
    age_min = (now - last_end).total_seconds() / 60
    local = now.astimezone(tz)
    in_session = local.weekday() < 5 and intraday.OPEN <= local.time() < intraday.CLOSE
    if in_session and age_min > intraday.DELAY_WARNING_MIN:
        state = "retrasado"
    elif in_session:
        state = "reciente"
    else:
        state = "mercado_cerrado"
    return {"state": state, "last_bar_end": last_end, "age_minutes": round(age_min, 1), "in_session": in_session, "note": DELAY_NOTE}


class IntradayView(APIView):
    throttle_classes = [IntradayThrottle]

    def get(self, request):
        ticker = str(request.query_params.get("ticker", "")).strip().upper()
        if not ticker:
            raise serializers.ValidationError({"ticker": "Indica un ticker."})
        if not InstrumentIdentifier.objects.filter(kind="TICKER", value__iexact=ticker).exists():
            raise serializers.ValidationError({"ticker": "Emisora no encontrada en tu catálogo."})
        range_ = request.query_params.get("range", "1d")
        try:
            or_minutes = int(request.query_params.get("or_minutes", 15))
        except ValueError:
            raise serializers.ValidationError({"or_minutes": "Entero múltiplo de 5."}) from None
        try:
            data = get_provider().fetch_intraday(ticker, range_)
        except ProviderError as exc:
            code = status.HTTP_503_SERVICE_UNAVAILABLE if exc.transient else status.HTTP_404_NOT_FOUND
            return Response({"detail": str(exc), "note": "El resto de la app sigue funcionando."}, status=code)
        cleaned = intraday.clean(data["raw"], data["timezone"])
        if not cleaned["bars"]:
            return Response(plain({"ticker": ticker, "evaluable": False, "hygiene": cleaned["hygiene"],
                                   "note": "Sin velas completas del horario regular."}))  # fmt: skip
        try:
            orb = intraday.orb(cleaned["bars"], or_minutes)
        except intraday.IntradayError as exc:
            raise serializers.ValidationError({"or_minutes": str(exc)}) from exc
        vw = intraday.vwap(cleaned["bars"])
        bars = [{"time": b["start"].isoformat(), "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"], "volume": b["volume"]}
                for b in cleaned["bars"]]  # fmt: skip
        return Response(
            plain(
                {
                    "ticker": ticker, "evaluable": True, "session": cleaned["session"], "currency": data["currency"],
                    "timezone": data["timezone"], "usable": cleaned["usable"], "missing_slots": cleaned["missing_slots"],
                    "hygiene": cleaned["hygiene"], "bars": bars,
                    "vwap": [{"time": b["time"], "value": round(v, 4)} for b, v in zip(bars, vw, strict=True) if v is not None],
                    "orb": orb, "freshness": _freshness(cleaned, data["timezone"]),
                    "note": "Análisis descriptivo; no es una recomendación." + (" La sesión tiene velas faltantes: no se considera usable." if not cleaned["usable"] else ""),
                }
            )
        )
