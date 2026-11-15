from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .engine import CalcError
from .service import MODES, calculate, target_buy_price, to_csv

INPUT_ERRORS = (CalcError, TypeError, ValueError, KeyError, AttributeError)


def _respond(request, compute):
    try:
        data = compute()
    except INPUT_ERRORS as exc:
        msg = str(exc) if isinstance(exc, CalcError) else "Parámetros con formato inválido (revisa tipos y campos)."
        raise serializers.ValidationError({"detail": msg}) from exc
    fmt = request.query_params.get("format", "json")
    if fmt == "csv":
        resp = HttpResponse(to_csv(data), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="hipotesis-{data["mode"]}.csv"'
        return resp
    if fmt != "json":
        raise serializers.ValidationError({"format": "Usa json o csv."})
    return Response({**data, "computed_at": timezone.now()})


class CagrView(APIView):
    """Modos: historical, projected (con escenarios opcionales), real, fx, twr y dividends."""

    def post(self, request):
        body = dict(request.data) if isinstance(request.data, dict) else {}
        mode = body.pop("mode", None)
        if mode not in MODES:
            raise serializers.ValidationError({"mode": f"Debe ser uno de {', '.join(MODES)}."})
        return _respond(request, lambda: calculate(mode, body))


class TargetBuyPriceView(APIView):
    def post(self, request):
        body = dict(request.data) if isinstance(request.data, dict) else {}
        return _respond(request, lambda: target_buy_price(body))
