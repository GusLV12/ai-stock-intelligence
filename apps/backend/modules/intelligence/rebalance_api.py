from decimal import Decimal, InvalidOperation

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.valuations.fair_value import plain

from .holdings import load_holdings
from .models import active_policy
from .rebalance import RebalanceError, propose

MAX_TARGETS = 100


def _money(value, field, default=Decimal(0)):
    if value in (None, ""):
        return default
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise serializers.ValidationError({field: "Número inválido."}) from None
    if not d.is_finite() or d < 0 or d > Decimal("1e12"):
        raise serializers.ValidationError({field: "Debe ser un número no negativo razonable."})
    return d


def _targets(value) -> dict[str, Decimal]:
    if not isinstance(value, dict) or not value or len(value) > MAX_TARGETS:
        raise serializers.ValidationError({"targets": "Objeto {emisora: porcentaje} no vacío."})
    out = {}
    for k, v in value.items():
        try:
            d = Decimal(str(v))
        except InvalidOperation:
            raise serializers.ValidationError({"targets": f"Porcentaje inválido para {k}."}) from None
        if not d.is_finite() or not (0 <= d <= 100):
            raise serializers.ValidationError({"targets": f"El porcentaje de {k} debe estar entre 0 y 100."})
        out[str(k).strip().upper()] = d
    return out


class RebalancePreviewView(APIView):
    """Propuesta teórica de rebalanceo. Sin `targets` usa los pesos objetivo de la política activa."""

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        raw = data.get("targets")
        source = "request"
        if raw in (None, {}):
            policy = active_policy(request.user)
            if policy is None or not policy.target_weights:
                raise serializers.ValidationError(
                    {"targets": "Indica pesos objetivo o define una política con target_weights."}
                )
            raw, source = policy.target_weights, f"política «{policy.name}»"
        try:
            result = propose(
                load_holdings(request.user)["holdings"], _targets(raw),
                _money(data.get("contribution"), "contribution"), bool(data.get("allow_sells", False)),
                _money(data.get("commission_per_trade"), "commission_per_trade"),
            )  # fmt: skip
        except RebalanceError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        result["targets_source"] = source
        return Response(plain(result))
