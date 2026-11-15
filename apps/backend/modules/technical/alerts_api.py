from decimal import Decimal, InvalidOperation

from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.instruments.models import InstrumentIdentifier
from modules.valuations.fair_value import plain

from .alerts import MAX_ACTIVE_PER_USER, SIGNAL_IDS, evaluate
from .models import PriceAlert


def _data(a: PriceAlert) -> dict:
    ticker = next(
        iter(
            InstrumentIdentifier.objects.filter(instrument=a.instrument, kind="TICKER").values_list(
                "value", flat=True
            )
        ),
        a.instrument.name,
    )
    return {"id": a.id, "ticker": ticker, "kind": a.kind, "threshold": a.threshold, "signal_id": a.signal_id, "note": a.note,
            "active": a.active, "created_at": a.created_at, "triggered_at": a.triggered_at, "triggered_detail": a.triggered_detail}  # fmt: skip


class AlertsView(APIView):
    def get(self, request):
        return Response(
            plain(
                [
                    _data(a)
                    for a in PriceAlert.objects.filter(user=request.user).select_related("instrument")[:200]
                ]
            )
        )

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        ticker = str(data.get("ticker", "")).strip().upper()
        ident = (
            InstrumentIdentifier.objects.filter(kind="TICKER", value__iexact=ticker)
            .select_related("instrument")
            .first()
        )
        if ident is None:
            raise serializers.ValidationError({"ticker": "Emisora no encontrada."})
        kind = data.get("kind")
        if kind not in PriceAlert.Kind.values:
            raise serializers.ValidationError(
                {"kind": f"Debe ser uno de {', '.join(PriceAlert.Kind.values)}."}
            )
        if PriceAlert.objects.filter(user=request.user, active=True).count() >= MAX_ACTIVE_PER_USER:
            raise serializers.ValidationError({"detail": f"Máximo {MAX_ACTIVE_PER_USER} alertas activas."})
        threshold, signal_id = None, ""
        if kind == PriceAlert.Kind.SIGNAL:
            signal_id = str(data.get("signal_id", ""))
            if signal_id not in SIGNAL_IDS:
                raise serializers.ValidationError(
                    {"signal_id": f"Debe ser uno de {', '.join(sorted(SIGNAL_IDS))}."}
                )
        else:
            try:
                threshold = Decimal(str(data.get("threshold")))
            except InvalidOperation:
                raise serializers.ValidationError({"threshold": "Número inválido."}) from None
            if not threshold.is_finite() or threshold <= 0 or threshold > Decimal("1e12"):
                raise serializers.ValidationError({"threshold": "Debe ser mayor que cero."})
        note = str(data.get("note", ""))[:200]
        a = PriceAlert.objects.create(
            user=request.user,
            instrument=ident.instrument,
            kind=kind,
            threshold=threshold,
            signal_id=signal_id,
            note=note,
        )
        return Response(plain(_data(a)), status=status.HTTP_201_CREATED)


def _own(request, pk):
    a = PriceAlert.objects.filter(pk=pk, user=request.user).select_related("instrument").first()
    if a is None:
        raise NotFound("Alerta no encontrada.")
    return a


class CancelAlertView(APIView):
    def post(self, request, pk):
        a = _own(request, pk)
        if a.active:
            a.active = False
            a.save(update_fields=["active"])
        return Response(plain(_data(a)))


class EvaluateAlertsView(APIView):
    """Evalúa tus alertas activas ahora con los precios y velas guardados (no consulta proveedores)."""

    def post(self, request):
        active = PriceAlert.objects.filter(user=request.user, active=True).select_related("instrument")
        fired = evaluate(list(active))
        return Response(plain({"evaluated": active.count() + len(fired), "triggered": [_data(a) for a in fired],
                               "note": "Se evalúa con datos ya guardados; no se envía ninguna notificación."}))  # fmt: skip
