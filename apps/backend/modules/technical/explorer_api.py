"""Exploración de cierres guardados y anotaciones personales del gráfico."""

import math
from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.intelligence.holdings import load_holdings

from . import indicators as ind
from .api import DEFAULT_DAYS, _bars_for, _freshness
from .models import ChartDrawing


class FilterSerializer(serializers.Serializer):
    universe = serializers.ChoiceField(choices=["portfolio", "research"], default="portfolio")
    currency = serializers.RegexField(r"^[A-Z]{3}$", default="USD")
    min_price = serializers.DecimalField(max_digits=20, decimal_places=4, min_value=0, required=False)
    max_price = serializers.DecimalField(max_digits=20, decimal_places=4, min_value=0, required=False)
    min_volume = serializers.FloatField(min_value=0, required=False)
    rsi_min = serializers.FloatField(min_value=0, max_value=100, required=False)
    rsi_max = serializers.FloatField(min_value=0, max_value=100, required=False)
    ema = serializers.ChoiceField(choices=["above", "below"], required=False)
    max_age = serializers.IntegerField(min_value=0, max_value=36500, required=False)

    def validate(self, attrs):
        for key in ("min_volume", "rsi_min", "rsi_max"):
            if key in attrs and not math.isfinite(attrs[key]):
                raise serializers.ValidationError({key: "Debe ser un número finito."})
        for lo, hi in (("min_price", "max_price"), ("rsi_min", "rsi_max")):
            if lo in attrs and hi in attrs and attrs[lo] > attrs[hi]:
                raise serializers.ValidationError({lo: "El mínimo no puede superar al máximo."})
        return attrs


class ExplorerView(APIView):
    def get(self, request):
        ser = FilterSerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)
        f = ser.validated_data
        held_ids = {
            h.instrument_id
            for h in load_holdings(request.user)["holdings"]
            if h.instrument_id and h.asset_class != "CASH"
        }
        instruments = Instrument.objects.filter(currency=f["currency"])
        instruments = (
            instruments.filter(id__in=held_ids)
            if f["universe"] == "portfolio"
            else instruments.filter(research_only=True)
        )
        idents = (
            InstrumentIdentifier.objects.filter(kind="TICKER", instrument__in=instruments)
            .select_related("instrument")
            .order_by("value", "instrument_id")
        )
        seen, rows, unavailable = set(), [], []
        for ident in idents:
            if ident.instrument_id in seen:
                continue
            seen.add(ident.instrument_id)
            bars, provider = _bars_for(ident.instrument_id, DEFAULT_DAYS)
            if len(bars) < 2:
                unavailable.append(
                    {
                        "instrument_id": ident.instrument_id,
                        "ticker": ident.value,
                        "reason": "Sin historial suficiente.",
                    }
                )
                continue
            closes = [b["close"] for b in bars]
            last = closes[-1]
            freshness = _freshness(bars, provider)
            rsi = ind.rsi(closes, 14)[-1]
            ema50, ema200 = ind.ema(closes, 50)[-1], ind.ema(closes, 200)[-1]
            volumes = [b["volume"] for b in bars[-20:]]
            avg_volume = (
                sum(volumes) / 20 if len(volumes) == 20 and all(v is not None for v in volumes) else None
            )
            missing = []
            if "min_volume" in f and avg_volume is None:
                missing.append("volumen")
            if ("rsi_min" in f or "rsi_max" in f) and rsi is None:
                missing.append("RSI")
            if "ema" in f and (ema50 is None or ema200 is None):
                missing.append("EMA 50/200")
            if missing:
                unavailable.append(
                    {
                        "instrument_id": ident.instrument_id,
                        "ticker": ident.value,
                        "reason": "Faltan datos: " + ", ".join(missing),
                    }
                )
                continue
            if ("min_price" in f and Decimal(str(last)) < f["min_price"]) or (
                "max_price" in f and Decimal(str(last)) > f["max_price"]
            ):
                continue
            if "min_volume" in f and avg_volume < f["min_volume"]:
                continue
            if ("rsi_min" in f and rsi < f["rsi_min"]) or ("rsi_max" in f and rsi > f["rsi_max"]):
                continue
            if (
                f.get("ema") == "above"
                and not ema50 > ema200
                or f.get("ema") == "below"
                and not ema50 < ema200
            ):
                continue
            if "max_age" in f and freshness["age_days"] > f["max_age"]:
                continue
            rows.append(
                {
                    "instrument_id": ident.instrument_id,
                    "ticker": ident.value,
                    "name": ident.instrument.name,
                    "currency": ident.instrument.currency,
                    "last_close": last,
                    "change_pct": (last / closes[-2] - 1) * 100 if closes[-2] else None,
                    "rsi": rsi,
                    "avg_volume_shares": avg_volume,
                    "freshness": freshness,
                }
            )
        return Response(
            {
                "rows": rows,
                "unavailable": unavailable,
                "note": "Cierres históricos; no son tiempo real ni recomendaciones.",
            }
        )


class PointSerializer(serializers.Serializer):
    time = serializers.DateField()
    price = serializers.DecimalField(max_digits=24, decimal_places=8, min_value=Decimal("0.00000001"))


class DrawingSerializer(serializers.ModelSerializer):
    points = PointSerializer(many=True, allow_empty=False)
    instrument_id = serializers.PrimaryKeyRelatedField(source="instrument", queryset=Instrument.objects.all())

    class Meta:
        model = ChartDrawing
        fields = ["id", "instrument_id", "kind", "points", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        points = attrs["points"]
        if len(points) != (1 if attrs["kind"] == "horizontal" else 2):
            raise serializers.ValidationError(
                {"points": "Se necesita un punto para un nivel o dos para una línea."}
            )
        if len(points) == 2 and points[0]["time"] == points[1]["time"]:
            raise serializers.ValidationError({"points": "Los puntos deben tener fechas distintas."})
        attrs["points"] = [
            {"time": p["time"].isoformat(), "price": str(p["price"])}
            for p in sorted(points, key=lambda p: p["time"])
        ]
        return attrs


class DrawingsView(APIView):
    def get(self, request):
        instrument_id = request.query_params.get("instrument_id", "")
        if not instrument_id.isdigit():
            raise serializers.ValidationError({"instrument_id": "Indica un instrumento."})
        return Response(
            DrawingSerializer(
                ChartDrawing.objects.filter(user=request.user, instrument_id=instrument_id), many=True
            ).data
        )

    def post(self, request):
        ser = DrawingSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        drawing = ser.save(user=request.user)
        return Response(DrawingSerializer(drawing).data, status=status.HTTP_201_CREATED)


class DrawingDetailView(APIView):
    def delete(self, request, pk):
        get_object_or_404(ChartDrawing, pk=pk, user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
