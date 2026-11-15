from decimal import Decimal

from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.instruments.models import Instrument
from modules.pricing.services import latest_price
from modules.valuations.fair_value import plain
from modules.valuations.models import Valuation

from .thesis_models import InvestmentThesis, ThesisFollowUp

STRATEGIES = ("long_term", "swing", "day_trading")


def _text(data, key, max_len, required=True):
    v = data.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise serializers.ValidationError({key: "Requerido."})
        return ""
    if not isinstance(v, str) or len(v) > max_len:
        raise serializers.ValidationError({key: f"Texto de hasta {max_len} caracteres."})
    return v.strip()


def _price_for(instrument):
    mp = latest_price(instrument) if instrument else None
    if mp is None:
        return None, {}
    return mp.price, {"provider": mp.provider, "observed_at": mp.observed_at.isoformat()}


def _follow_data(f: ThesisFollowUp, base_price) -> dict:
    change = None
    if base_price and f.price is not None and base_price > 0:
        change = (f.price - base_price) / base_price
    return {"id": f.id, "status": f.status, "note": f.note, "price": f.price, "price_source": f.price_source,
            "change_since_thesis": change, "created_at": f.created_at}  # fmt: skip


def _summary(t: InvestmentThesis) -> dict:
    last = t.follow_ups.last()
    return {"id": t.id, "title": t.title, "instrument": t.instrument_id, "strategy": t.strategy, "horizon": t.horizon,
            "price": t.price, "status": last.status if last else "OPEN", "follow_ups": t.follow_ups.count(),
            "created_at": t.created_at}  # fmt: skip


class ThesesView(APIView):
    def get(self, request):
        return Response(
            plain([_summary(t) for t in InvestmentThesis.objects.filter(user=request.user)[:100]])
        )

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        strategy = data.get("strategy", "long_term")
        if strategy not in STRATEGIES:
            raise serializers.ValidationError({"strategy": f"Debe ser uno de {', '.join(STRATEGIES)}."})
        instrument = None
        if data.get("instrument") not in (None, ""):
            instrument = Instrument.objects.filter(pk=data["instrument"]).first()
            if instrument is None:
                raise serializers.ValidationError({"instrument": "Instrumento no encontrado."})
        valuation_id = data.get("valuation_id")
        if (
            valuation_id not in (None, "")
            and not Valuation.objects.filter(pk=valuation_id, user=request.user).exists()
        ):
            raise serializers.ValidationError({"valuation_id": "Valoración no encontrada."})
        sources = data.get("sources") or []
        if (
            not isinstance(sources, list)
            or len(sources) > 20
            or any(not isinstance(s, str) or len(s) > 300 for s in sources)
        ):
            raise serializers.ValidationError(
                {"sources": "Lista de hasta 20 textos de máximo 300 caracteres."}
            )
        price, source = _price_for(instrument)
        t = InvestmentThesis.objects.create(
            user=request.user, instrument=instrument, title=_text(data, "title", 150), strategy=strategy,
            horizon=_text(data, "horizon", 60, required=False), thesis=_text(data, "thesis", 5000),
            risks=_text(data, "risks", 3000), invalidation=_text(data, "invalidation", 3000),
            price=price, price_source=source, valuation_id=int(valuation_id) if valuation_id not in (None, "") else None,
            sources=sources,
        )  # fmt: skip
        return Response(plain({**_summary(t), "price_source": source}), status=status.HTTP_201_CREATED)


def _own(request, pk):
    t = InvestmentThesis.objects.filter(pk=pk, user=request.user).first()
    if t is None:
        raise NotFound("Tesis no encontrada.")
    return t


class ThesisDetailView(APIView):
    def get(self, request, pk):
        t = _own(request, pk)
        return Response(
            plain(
                {
                    **_summary(t),
                    "thesis": t.thesis,
                    "risks": t.risks,
                    "invalidation": t.invalidation,
                    "price_source": t.price_source,
                    "valuation_id": t.valuation_id,
                    "sources": t.sources,
                    "follow_ups": [_follow_data(f, t.price) for f in t.follow_ups.all()],
                }
            )
        )


class FollowUpView(APIView):
    def post(self, request, pk):
        t = _own(request, pk)
        data = request.data if isinstance(request.data, dict) else {}
        st = data.get("status", "OPEN")
        if st not in ThesisFollowUp.Status.values:
            raise serializers.ValidationError(
                {"status": f"Debe ser uno de {', '.join(ThesisFollowUp.Status.values)}."}
            )
        price, source = _price_for(t.instrument)
        f = ThesisFollowUp.objects.create(
            thesis=t, status=st, note=_text(data, "note", 3000), price=price, price_source=source
        )
        base: Decimal | None = t.price
        return Response(plain(_follow_data(f, base)), status=status.HTTP_201_CREATED)
