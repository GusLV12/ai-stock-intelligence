from decimal import Decimal, InvalidOperation

from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.portfolio.dashboard import build_dashboard
from modules.valuations.fair_value import plain

from . import exposure
from .holdings import load_holdings
from .models import PortfolioPolicy, active_policy

CLASSES = {"STOCK", "ADR", "ETF", "CRYPTO", "CASH"}
MAX_TARGETS = 100


def _pct(value, field, allow_none=True):
    if value is None or value == "":
        if allow_none:
            return None
        raise serializers.ValidationError({field: "Requerido."})
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise serializers.ValidationError({field: "Número inválido."}) from None
    if not d.is_finite() or not (0 <= d <= 100):
        raise serializers.ValidationError({field: "Debe estar entre 0 y 100."})
    return d


def _pct_map(value, field, valid_keys=None):
    if value in (None, ""):
        return {}
    if not isinstance(value, dict) or len(value) > MAX_TARGETS:
        raise serializers.ValidationError({field: "Debe ser un objeto {clave: porcentaje}."})
    out = {}
    for k, v in value.items():
        key = str(k).strip().upper()
        if valid_keys and key not in valid_keys:
            raise serializers.ValidationError({field: f"Clase desconocida: {key}. Usa {sorted(valid_keys)}."})
        out[key] = float(_pct(v, field, allow_none=False))
    return out


def _policy_dict(p: PortfolioPolicy) -> dict:
    d = p.as_dict()
    d["max_class_pct"] = {k: Decimal(str(v)) for k, v in d["max_class_pct"].items()}
    d["target_weights"] = {k: Decimal(str(v)) for k, v in d["target_weights"].items()}
    return d


class PoliciesView(APIView):
    def get(self, request):
        return Response(plain([{**_policy_dict(p), "created_at": p.created_at} for p in PortfolioPolicy.objects.filter(user=request.user)[:50]]))

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        objective = data.get("objective", "mixed")
        if objective not in exposure.OBJECTIVES:
            raise serializers.ValidationError({"objective": f"Debe ser uno de {', '.join(exposure.OBJECTIVES)}."})
        targets = _pct_map(data.get("target_weights"), "target_weights")
        if sum(targets.values()) > 100.0001:
            raise serializers.ValidationError({"target_weights": "Los objetivos suman más de 100%."})
        horizon = data.get("horizon_years")
        if horizon not in (None, "") and not (str(horizon).isdigit() and 1 <= int(horizon) <= 60):
            raise serializers.ValidationError({"horizon_years": "Entero de 1 a 60."})
        p = PortfolioPolicy.objects.create(
            user=request.user, name=str(data.get("name") or "Política")[:100], objective=objective,
            max_position_pct=_pct(data.get("max_position_pct"), "max_position_pct"),
            max_class_pct=_pct_map(data.get("max_class_pct"), "max_class_pct", CLASSES),
            min_cash_pct=_pct(data.get("min_cash_pct"), "min_cash_pct"),
            target_weights=targets, horizon_years=int(horizon) if horizon not in (None, "") else None,
        )  # fmt: skip
        return Response(plain({**_policy_dict(p), "created_at": p.created_at}), status=status.HTTP_201_CREATED)


class ExposureView(APIView):
    def get(self, request):
        loaded = load_holdings(request.user)
        policy = active_policy(request.user)
        result = exposure.analyze(loaded["holdings"], _policy_dict(policy) if policy else None)
        if result["evaluable"]:
            dash = build_dashboard(request.user)["totals_by_currency"].get("USD")
            if dash:
                dash_total = Decimal(dash["market_value"]) + Decimal(dash["cash"])
                # Solo comparable si todas las posiciones valoradas están en USD y ninguna quedó fuera.
                comparable = not loaded["unconverted"] and all(h.currency == "USD" for h in loaded["holdings"] if h.value_base is not None)
                result["reconciliation"].update(
                    {"dashboard_total_usd": dash_total, "difference": result["total_value_usd"] - dash_total, "comparable": comparable}
                )
        result["unpriced_positions"] = loaded["unpriced"]
        result["fx"] = loaded["fx"]
        result["disclaimer"] = "Exposición calculada con precios observados (no tiempo real). No es asesoría de inversión."
        return Response(plain(result))
