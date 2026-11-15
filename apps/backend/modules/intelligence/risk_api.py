from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.pricing.history_models import DailyClose
from modules.valuations.fair_value import plain

from . import risk
from .holdings import load_holdings

MIN_WINDOW, MAX_WINDOW, DEFAULT_WINDOW = 60, 1830, 365


def _series_for(instrument_ids, since):
    """Cierres por instrumento desde `since`; si hay varios proveedores se usa el que más datos aporta."""
    rows = DailyClose.objects.filter(instrument_id__in=instrument_ids, date__gte=since).order_by("date")
    per = defaultdict(lambda: defaultdict(list))
    for r in rows:
        per[r.instrument_id][r.provider].append((r.date, float(r.close)))
    out = {}
    for ins_id, by_provider in per.items():
        provider = max(by_provider, key=lambda p: len(by_provider[p]))
        out[ins_id] = by_provider[provider]
    return out


class RiskView(APIView):
    """Volatilidad, caída máxima y correlaciones históricas con ventana explícita (`window_days`)."""

    def get(self, request):
        raw = request.query_params.get("window_days", DEFAULT_WINDOW)
        try:
            window = int(raw)
        except (TypeError, ValueError):
            raise serializers.ValidationError({"window_days": "Entero de días."}) from None
        if not MIN_WINDOW <= window <= MAX_WINDOW:
            raise serializers.ValidationError({"window_days": f"Debe estar entre {MIN_WINDOW} y {MAX_WINDOW}."})
        since = timezone.now().date() - timedelta(days=window)

        loaded = load_holdings(request.user)
        holdings = [h for h in loaded["holdings"] if h.value_base is not None]
        total = sum((h.value_base for h in holdings), Decimal(0))
        if total <= 0:
            return Response({"evaluable": False, "note": "Sin posiciones valoradas: no hay riesgo que calcular."})
        by_instrument = {h.instrument_id: h for h in holdings if h.instrument_id}
        raw_series = _series_for(list(by_instrument), since)
        series = {by_instrument[i].ticker: bars for i, bars in raw_series.items()}
        weights = {h.ticker: float(h.value_base / total) for h in holdings}
        result = risk.analyze(series, weights)
        no_history = sorted(h.ticker for h in holdings if h.asset_class != "CASH" and h.ticker not in series)
        if no_history:
            result["warnings"].append(
                "Sin historial guardado (ejecuta refresh_price_history): " + ", ".join(no_history) + "."
            )
        result.update(
            evaluable=bool(result["assets"]), requested_window_days=window,
            providers=sorted(Counter(r.provider for r in DailyClose.objects.filter(instrument_id__in=list(by_instrument), date__gte=since).only("provider")).keys()),
            disclaimer="Métricas históricas, no predicen el riesgo futuro ni son asesoría de inversión.",
        )  # fmt: skip
        return Response(plain(_floats_to_decimal(result)))


def _floats_to_decimal(obj):
    """plain() serializa Decimal; las métricas en float se pasan a Decimal(str()) para tener texto estable."""
    if isinstance(obj, float):
        return Decimal(repr(round(obj, 10)))
    if isinstance(obj, dict):
        return {k: _floats_to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_floats_to_decimal(v) for v in obj]
    return obj
