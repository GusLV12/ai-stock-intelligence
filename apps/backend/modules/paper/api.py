from decimal import Decimal, InvalidOperation

from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.instruments.models import InstrumentIdentifier
from modules.quant.models import QuantExperiment
from modules.valuations.fair_value import plain

from . import services as svc
from .engine import PaperError
from .models import PaperAccount, PaperJournalEntry, PaperOrder

MAX_ACCOUNTS = 10
CONFIG_KEYS = {
    "slippage_bps": (0, 500),
    "commission_bps": (0, 500),
    "commission_min": (0, 100),
    "max_position_pct": (0.01, 1),
    "max_price_age_days": (1, 30),
}
DISCLAIMER = (
    "Paper trading: cuenta virtual de práctica. Ninguna ruta de esta sección envía órdenes a un broker ni mueve dinero real; "
    "no es una recomendación de inversión."
)


def _dec(data, key, required=True):
    raw = data.get(key)
    if raw in (None, ""):
        if required:
            raise serializers.ValidationError({key: "Es obligatorio."})
        return None
    try:
        v = Decimal(str(raw))
    except InvalidOperation:
        raise serializers.ValidationError({key: "Número inválido."}) from None
    if not v.is_finite():
        raise serializers.ValidationError({key: "Número inválido."})
    return v


def _mine(request, pk) -> PaperAccount:
    a = PaperAccount.objects.filter(user=request.user, pk=pk).first()
    if a is None:
        raise NotFound("Cuenta no encontrada.")
    return a


def _order(o: PaperOrder) -> dict:
    ticker = next(
        iter(
            InstrumentIdentifier.objects.filter(instrument_id=o.instrument_id, kind="TICKER").values_list(
                "value", flat=True
            )
        ),
        o.instrument.name,
    )
    return {
        "id": o.id, "ticker": ticker, "instrument": o.instrument_id, "side": o.side, "order_type": o.order_type, "quantity": o.quantity,
        "limit_price": o.limit_price, "status": o.status, "created_at": o.created_at, "decided_at": o.decided_at,
        "reference_price": o.reference_price, "reference_source": o.reference_source, "fill_price": o.fill_price, "fee": o.fee,
        "adverse_slippage_bps": o.adverse_slippage_bps, "reason": o.reason, "note": o.note, "source": o.source,
    }  # fmt: skip


class AccountsView(APIView):
    def get(self, request):
        out = []
        for a in PaperAccount.objects.filter(user=request.user):
            s = svc.summary(a)
            out.append(
                {
                    "id": a.id,
                    "name": a.name,
                    "currency": a.currency,
                    "initial_cash": a.initial_cash,
                    "equity": s["equity"],
                    "return_pct": s["return_pct"],
                    "valuation_complete": s["valuation_complete"],
                    "created_at": a.created_at,
                }
            )
        return Response(
            plain(
                {
                    "accounts": out,
                    "disclaimer": DISCLAIMER,
                    "defaults": {
                        "slippage_bps": 5,
                        "commission_bps": 0,
                        "max_position_pct": 0.35,
                        "max_price_age_days": 5,
                    },
                }
            )
        )

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        if PaperAccount.objects.filter(user=request.user).count() >= MAX_ACCOUNTS:
            raise serializers.ValidationError({"detail": f"Máximo {MAX_ACCOUNTS} cuentas."})
        cfg = {}
        for key, (lo, hi) in CONFIG_KEYS.items():
            v = _dec(
                data.get("config", {}) if isinstance(data.get("config"), dict) else {}, key, required=False
            )
            if v is not None:
                if not Decimal(str(lo)) <= v <= Decimal(str(hi)):
                    raise serializers.ValidationError({key: f"Debe estar entre {lo} y {hi}."})
                cfg[key] = v
        try:
            a = svc.create_account(
                request.user, str(data.get("name") or "").strip(), _dec(data, "initial_cash"), cfg
            )
        except PaperError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(plain(svc.summary(a)), status=status.HTTP_201_CREATED)


class AccountView(APIView):
    def get(self, request, pk):
        a = _mine(request, pk)
        return Response(plain({**svc.summary(a), "disclaimer": DISCLAIMER}))


class OrdersView(APIView):
    def get(self, request, pk):
        a = _mine(request, pk)
        return Response(plain([_order(o) for o in a.orders.select_related("instrument")[:300]]))

    def post(self, request, pk):
        a = _mine(request, pk)
        data = request.data if isinstance(request.data, dict) else {}
        identifiers = list(
            InstrumentIdentifier.objects.filter(
                kind="TICKER", value__iexact=str(data.get("ticker", "")).strip()
            )
            .select_related("instrument")
        )
        if not identifiers:
            raise serializers.ValidationError({"ticker": "Emisora no encontrada."})
        if len({i.instrument_id for i in identifiers}) > 1:
            raise serializers.ValidationError({"ticker": "La emisora es ambigua entre mercados; no se puede simular esta orden."})
        ident = identifiers[0]
        try:
            o = svc.place_order(
                a, ident.instrument, str(data.get("side", "")).upper(), _dec(data, "quantity"), str(data.get("order_type", "MARKET")).upper(),
                _dec(data, "limit_price", required=False), note=str(data.get("note", "")), source=_source(request, data),
            )  # fmt: skip
        except PaperError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(plain(_order(o)), status=status.HTTP_201_CREATED)


def _source(request, data) -> dict:
    exp = data.get("experiment")
    if exp in (None, ""):
        return {}
    if not isinstance(exp, int) or not QuantExperiment.objects.filter(pk=exp, user=request.user).exists():
        raise serializers.ValidationError({"experiment": "Experimento no encontrado."})
    return {"kind": "quant", "experiment": exp}


class CancelOrderView(APIView):
    def post(self, request, pk, order_id):
        o = (
            PaperOrder.objects.filter(pk=order_id, account=_mine(request, pk))
            .select_related("instrument")
            .first()
        )
        if o is None:
            raise NotFound("Orden no encontrada.")
        try:
            return Response(plain(_order(svc.cancel_order(o))))
        except PaperError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc


class ProcessView(APIView):
    def post(self, request, pk):
        return Response(plain(svc.process_pending(_mine(request, pk))))


class EquityView(APIView):
    def get(self, request, pk):
        a = _mine(request, pk)
        return Response(
            plain(
                {
                    "curve": [{"date": c["date"], "equity": c["equity"]} for c in svc.equity_curve(a)],
                    "metrics": svc.metrics(a),
                }
            )
        )


class JournalView(APIView):
    def get(self, request, pk):
        a = _mine(request, pk)
        return Response(
            plain(
                [
                    {
                        "id": e.id,
                        "kind": e.kind,
                        "text": e.text,
                        "order": e.order_id,
                        "created_at": e.created_at,
                    }
                    for e in a.journal.all()[:200]
                ]
            )
        )

    def post(self, request, pk):
        a = _mine(request, pk)
        data = request.data if isinstance(request.data, dict) else {}
        text = str(data.get("text", "")).strip()
        kind = data.get("kind", "NOTE")
        if not 1 <= len(text) <= 3000:
            raise serializers.ValidationError({"text": "Entre 1 y 3000 caracteres."})
        if kind not in PaperJournalEntry.Kind.values:
            raise serializers.ValidationError(
                {"kind": f"Debe ser uno de {', '.join(PaperJournalEntry.Kind.values)}."}
            )
        order = None
        if data.get("order") not in (None, ""):
            order = PaperOrder.objects.filter(
                pk=data["order"] if isinstance(data["order"], int) else -1, account=a
            ).first()
            if order is None:
                raise serializers.ValidationError({"order": "Orden no encontrada en esta cuenta."})
        e = PaperJournalEntry.objects.create(account=a, order=order, kind=kind, text=text)
        return Response(
            plain(
                {"id": e.id, "kind": e.kind, "text": e.text, "order": e.order_id, "created_at": e.created_at}
            ),
            status=status.HTTP_201_CREATED,
        )


class ProposalView(APIView):
    def get(self, request, pk):
        a = _mine(request, pk)
        try:
            exp_id, top_k = (
                int(request.query_params.get("experiment", "")),
                int(request.query_params.get("top_k", 3)),
            )
        except ValueError:
            raise serializers.ValidationError(
                {"experiment": "Indica el experimento y un top_k numérico."}
            ) from None
        if not 1 <= top_k <= 20:
            raise serializers.ValidationError({"top_k": "Entre 1 y 20."})
        exp = QuantExperiment.objects.filter(pk=exp_id, user=request.user).first()
        if exp is None:
            raise NotFound("Experimento no encontrado.")
        return Response(plain(svc.propose_from_experiment(a, exp, top_k)))
