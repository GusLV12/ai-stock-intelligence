"""Liquidez por emisora y de toda la cartera (solo lectura; usa los cierres diarios guardados)."""

from collections import defaultdict
from decimal import Decimal

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.intelligence.holdings import load_holdings
from modules.valuations.fair_value import plain

from . import liquidity as lq
from .api import _bars_for, _find_instrument, _freshness

MAX_DAYS = 600


def _params(request):
    q = request.query_params
    try:
        window = int(q.get("window", lq.DEFAULT_WINDOW))
        participation = float(q.get("participation", lq.DEFAULT_PARTICIPATION))
        quantity = None if q.get("quantity") in (None, "") else float(q.get("quantity"))
    except ValueError:
        raise serializers.ValidationError(
            {"detail": "window, participation y quantity deben ser numéricos."}
        ) from None
    return window, participation, quantity


def _result(ticker, instrument_id, window, participation, quantity):
    bars, provider = _bars_for(instrument_id, MAX_DAYS)
    if len(bars) < lq.MIN_BARS:
        return {"ticker": ticker, "evaluable": False,
                "reason": "Sin historial suficiente: ejecuta refresh_price_history."}  # fmt: skip
    res = lq.analyze(bars, window, quantity, participation)
    res["ticker"] = ticker
    res["freshness"] = _freshness(bars, provider)
    if not all(b["high"] is not None for b in bars[-window:]):
        res["caveats"].append(
            "Faltan máximos/mínimos en parte de la ventana: el spread estimado puede no ser evaluable."
        )
    return res


class LiquidityView(APIView):
    """GET ?ticker=AAPL&window=20&quantity=100&participation=0.1"""

    def get(self, request):
        ticker = str(request.query_params.get("ticker", "")).strip().upper()
        if not ticker:
            raise serializers.ValidationError({"ticker": "Indica un ticker."})
        window, participation, quantity = _params(request)
        instrument = _find_instrument(ticker, request.query_params.get("instrument_id"))
        if instrument is None:
            raise serializers.ValidationError({"ticker": "Emisora no encontrada."})
        try:
            return Response(plain(_result(ticker, instrument.id, window, participation, quantity)))
        except lq.LiquidityError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc


class PortfolioLiquidityView(APIView):
    """Liquidez de cada posición real frente a su volumen negociado (suma de todas las cuentas de la emisora)."""

    def get(self, request):
        window, participation, _ = _params(request)
        qty, ids = defaultdict(Decimal), {}
        for h in load_holdings(request.user)["holdings"]:
            if h.asset_class == "CASH" or not h.instrument_id:
                continue
            qty[h.ticker] += h.quantity
            ids[h.ticker] = h.instrument_id
        rows = []
        try:
            for ticker, q in qty.items():
                r = _result(ticker, ids[ticker], window, participation, float(q))
                rows.append(r)
        except lq.LiquidityError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc

        def days(r):
            p = r.get("position") or {}
            return p.get("days_to_trade_at_participation") or -1

        rows.sort(key=lambda r: (-days(r), r["ticker"]))
        flags = [(r.get("position") or {}).get("size_flag") for r in rows]
        return Response(plain({
            "window_days": window,
            "participation_assumed": participation,
            "rows": rows,
            "summary": {
                "positions": len(rows),
                "evaluable": sum(1 for r in rows if r.get("evaluable")),
                "size_high": flags.count("alto"),
                "size_visible": flags.count("visible"),
                "not_evaluable": [r["ticker"] for r in rows if not r.get("evaluable")],
            },
            "disclaimer": "Liquidez histórica estimada con cierres diarios; no es una garantía de ejecución ni asesoría de inversión.",
        }))  # fmt: skip
