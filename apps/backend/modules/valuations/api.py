from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.instruments.models import Instrument
from modules.pricing.services import latest_price

from . import engine
from .engine import ValuationError
from .fair_value import MODEL_VERSION, D, asi_fair_value_v1, parse_dcf, plain
from .models import Valuation

INPUT_ERRORS = (ValuationError, TypeError, ValueError, KeyError, AttributeError)


def _bad(exc: Exception):
    msg = (
        str(exc)
        if isinstance(exc, ValuationError)
        else "Supuestos con formato inválido (revisa tipos y campos)."
    )
    raise serializers.ValidationError({"detail": msg}) from exc


def _summary(v: Valuation) -> dict:
    out = v.output
    return {
        "id": v.id, "label": v.label, "method": v.method, "model_version": v.model_version,
        "instrument": v.instrument_id, "created_at": v.created_at,
        "evaluable": out.get("evaluable"), "central_value": out.get("central_value"),
        "market_price": out.get("market_price"), "margin_of_safety_base": out.get("margin_of_safety_base"),
    }  # fmt: skip


def _calculate_fair_value(request):
    data = dict(request.data) if isinstance(request.data, dict) else {}
    if not data:
        raise serializers.ValidationError({"detail": "Envía los supuestos en JSON."})
    instrument, price_source = None, None
    if (inst_id := data.get("instrument")) is not None:
        instrument = Instrument.objects.filter(pk=inst_id).first()
        if instrument is None:
            raise serializers.ValidationError({"instrument": "Instrumento no encontrado."})
        if data.get("currency") and str(data["currency"]).upper() != instrument.currency.upper():
            raise serializers.ValidationError({"currency": "La moneda no coincide con la del instrumento."})
        if data.get("market_price") is None:
            mp = latest_price(instrument)
            if mp is None:
                raise serializers.ValidationError(
                    {"market_price": "Indica el precio: el instrumento no tiene precios."}
                )
            if data.get("currency") and str(data["currency"]).upper() != mp.currency.upper():
                raise serializers.ValidationError({"currency": "La moneda no coincide con el precio registrado."})
            expected = data.pop("expected_price_observed_at", None)
            expected_price = data.pop("expected_market_price", None)
            expected_provider = data.pop("expected_price_provider", None)
            try:
                stale = (expected and parse_datetime(str(expected)) != mp.observed_at) or (
                    expected_price is not None and D(expected_price, "expected_market_price") != mp.price
                ) or (expected_provider is not None and expected_provider != mp.provider)
            except (ValuationError, ValueError):
                stale = True
            if stale:
                raise serializers.ValidationError({"market_price": "El precio registrado cambió; vuelve a calcular."})
            data["market_price"] = str(mp.price)
            data.setdefault("currency", mp.currency)
            price_source = {"provider": mp.provider, "observed_at": mp.observed_at.isoformat()}
        data.setdefault("instrument_class", instrument.asset_class)
    try:
        result = asi_fair_value_v1(data)
    except INPUT_ERRORS as exc:
        _bad(exc)
    output = plain(result)
    if price_source:
        output["price_source"] = price_source
    return data, instrument, output


class FairValuePreviewView(APIView):
    """Calcula con las mismas reglas del guardado, sin crear una valoración."""

    def post(self, request):
        _, _, output = _calculate_fair_value(request)
        return Response(output)


class FairValueView(APIView):
    def get(self, request):
        return Response([_summary(v) for v in Valuation.objects.filter(user=request.user)[:100]])

    def post(self, request):
        data, instrument, output = _calculate_fair_value(request)
        label = str(data.get("label", ""))[:120]
        inputs = {k: v for k, v in data.items() if k not in ("label",)}
        v = Valuation.objects.create(
            user=request.user, instrument=instrument, label=label, model_version=MODEL_VERSION,
            inputs=inputs, output=output,
        )  # fmt: skip
        return Response({"id": v.id, "created_at": v.created_at, **output}, status=status.HTTP_201_CREATED)


class FairValueDetailView(APIView):
    def get(self, request, pk):
        v = Valuation.objects.filter(pk=pk, user=request.user).first()
        if v is None:
            raise NotFound("Valoración no encontrada.")
        return Response(
            {
                "id": v.id,
                "label": v.label,
                "model_version": v.model_version,
                "created_at": v.created_at,
                "inputs": v.inputs,
                "result": v.output,
            }
        )


class DcfView(APIView):
    """Calculadora DCF suelta (con sensibilidad); no guarda nada."""

    def post(self, request):
        try:
            block = request.data if isinstance(request.data, dict) else {}
            params = parse_dcf(block)
            res = engine.dcf(**params)
            if res["applicable"]:
                res["sensitivity"] = engine.sensitivity_wacc_g(params)
        except INPUT_ERRORS as exc:
            _bad(exc)
        return Response({"computed_at": timezone.now(), "assumptions_version": MODEL_VERSION, **plain(res)})


class ReverseDcfView(APIView):
    """¿Qué crecimiento implícito justifica el precio? Supuestos DCF sin `growth` más `price`."""

    def post(self, request):
        try:
            block = dict(request.data) if isinstance(request.data, dict) else {}
            price = D(block.pop("price", None), "price")
            block["growth"] = 0  # marcador: reverse_dcf resuelve el crecimiento
            params = parse_dcf(block)
            params.pop("growth")
            res = engine.reverse_dcf(price, params)
        except INPUT_ERRORS as exc:
            _bad(exc)
        return Response({"computed_at": timezone.now(), "assumptions_version": MODEL_VERSION, **plain(res)})
