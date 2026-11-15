from datetime import datetime
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.instruments.models import Instrument

from .models import FxRate, MarketPrice
from .services import PricingError, latest_fx, latest_price, record_fx, record_price

MANUAL = "MANUAL"  # por API solo se aceptan observaciones manuales: la procedencia no se falsifica


def _price_data(mp: MarketPrice):
    return {
        "instrument": mp.instrument_id,
        "price": str(mp.price.normalize()),
        "currency": mp.currency,
        "provider": mp.provider,
        "observed_at": mp.observed_at,
        "age_hours": round((timezone.now() - mp.observed_at).total_seconds() / 3600, 1),
    }


def _fx_data(fx: FxRate):
    return {
        "pair": f"{fx.base}/{fx.quote}",
        "rate": str(fx.rate.normalize()),
        "provider": fx.provider,
        "observed_at": fx.observed_at,
        "age_hours": round((timezone.now() - fx.observed_at).total_seconds() / 3600, 1),
    }


def _parse_when(raw):
    try:
        when = datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise serializers.ValidationError({"observed_at": "Fecha ISO 8601 inválida."}) from exc
    if when.tzinfo is None:
        raise serializers.ValidationError({"observed_at": "Debe incluir zona horaria."})
    if when > timezone.now():
        raise serializers.ValidationError({"observed_at": "No puede estar en el futuro."})
    return when


def _parse_decimal(raw, field):
    try:
        d = Decimal(str(raw))
    except Exception as exc:
        raise serializers.ValidationError({field: "Número inválido."}) from exc
    if not d.is_finite():
        raise serializers.ValidationError({field: "Número inválido."})
    return d


class PricesView(APIView):
    def get(self, request):
        if inst_id := request.query_params.get("instrument"):
            ins = Instrument.objects.filter(pk=inst_id).first()
            mp = latest_price(ins) if ins else None
            if mp is None:
                raise NotFound("Sin precio registrado para ese instrumento.")
            return Response(_price_data(mp))
        out = [
            _price_data(mp)
            for ins in Instrument.objects.order_by("id")
            if (mp := latest_price(ins)) is not None
        ]
        return Response(out)

    def post(self, request):
        ins = Instrument.objects.filter(pk=request.data.get("instrument")).first()
        if ins is None:
            raise serializers.ValidationError({"instrument": "Instrumento no encontrado."})
        price = _parse_decimal(request.data.get("price"), "price")
        when = _parse_when(request.data.get("observed_at"))
        try:
            mp, created = record_price(ins, price, MANUAL, when)
        except PricingError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(_price_data(mp), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class FxView(APIView):
    def get(self, request):
        base, quote = request.query_params.get("base", "USD"), request.query_params.get("quote", "MXN")
        fx = latest_fx(base, quote)
        if fx is None:
            raise NotFound("Sin tipo de cambio registrado para ese par.")
        return Response(_fx_data(fx))

    def post(self, request):
        rate = _parse_decimal(request.data.get("rate"), "rate")
        when = _parse_when(request.data.get("observed_at"))
        try:
            fx, created = record_fx(
                str(request.data.get("base", "")), str(request.data.get("quote", "")), rate, MANUAL, when
            )
        except PricingError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(_fx_data(fx), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
