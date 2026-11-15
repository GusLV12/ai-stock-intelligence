from decimal import Decimal, InvalidOperation

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.valuations.fair_value import plain

from .holdings import load_holdings
from .shocks import PRESETS, ShockError, simulate, validate_shocks


class PresetsView(APIView):
    def get(self, request):
        return Response([{"id": k, **v} for k, v in PRESETS.items()])


def scenario_report(user, data: dict, account_ids=None) -> dict:
    """Shocks deterministas sobre las posiciones (opcionalmente solo de ciertas cuentas). Lanza ShockError."""
    shocks = list(data.get("shocks") or [])
    fx_pct = data.get("fx_usd_mxn_pct")
    if preset := data.get("preset"):
        if preset not in PRESETS:
            raise ShockError(f"Preset desconocido: {preset}.")
        shocks = [*PRESETS[preset]["shocks"], *shocks]
        fx_pct = fx_pct if fx_pct is not None else PRESETS[preset].get("fx_usd_mxn_pct")
    try:
        fx_pct = None if fx_pct in (None, "") else Decimal(str(fx_pct))
    except InvalidOperation:
        raise ShockError("fx_usd_mxn_pct: número inválido.") from None
    if fx_pct is not None and not fx_pct.is_finite():
        raise ShockError("fx_usd_mxn_pct: número inválido.")
    loaded = load_holdings(user, account_ids=account_ids)
    fx = loaded["fx"]
    result = simulate(loaded["holdings"], validate_shocks(shocks), fx_pct, fx["rate"] if fx else None)
    result["unpriced_positions"] = loaded["unpriced"]
    result["fx"] = fx
    return plain(result)


class ScenarioView(APIView):
    """Shocks deterministas. Cuerpo: {"preset": "...", "shocks": [{"target","pct"}], "fx_usd_mxn_pct": 5}."""

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        try:
            return Response(scenario_report(request.user, data))
        except ShockError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
