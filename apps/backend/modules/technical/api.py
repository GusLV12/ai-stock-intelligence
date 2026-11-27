from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db.models import Count, Max, Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.instruments.models import InstrumentIdentifier
from modules.intelligence.holdings import load_holdings
from modules.portfolio.models import TaxLot
from modules.pricing.history_models import DailyClose
from modules.valuations.fair_value import plain

from . import indicators as ind
from . import trade_plan
from .models import TechnicalClose
from .signals import DEFAULTS, SignalError, analyze

MAX_DAYS, DEFAULT_DAYS = 5000, 300
STALE_DAYS = 4  # un fin de semana largo ya es normal; más que eso se marca
DELAY_NOTE = "Cierres diarios de un proveedor gratuito: no son tiempo real y pueden tener retraso."


def _held_instrument_ids(user):
    """Mismos pares del libro que _positions_for, sin cargar valoraciones para un selector."""
    return set(
        TaxLot.objects.filter(account__owner=user)
        .exclude(instrument__asset_class="CASH")
        .values_list("instrument_id", flat=True)
        .distinct()
    )


def _bars_from_rows(rows, days):
    if not rows:
        return [], None
    by_provider = defaultdict(list)
    for r in rows:
        by_provider[r.provider].append(r)
    provider = max(by_provider, key=lambda p: len(by_provider[p]))
    chosen = by_provider[provider][-days:]
    f = lambda v: None if v is None else float(v)
    return [
        {
            "date": r.date,
            "open": f(r.open),
            "high": f(r.high),
            "low": f(r.low),
            "close": float(r.close),
            "volume": r.volume,
        }
        for r in chosen
    ], provider


def _merge_bars(technical, legacy):
    """Conserva el historial anterior del mismo proveedor sin modificar ninguna tabla."""
    if not technical:
        return legacy
    providers = defaultdict(list)
    for row in technical:
        providers[row.provider].append(row)
    provider = max(providers, key=lambda key: len(providers[key]))
    current = providers[provider]
    earliest = current[0].date
    previous = [row for row in legacy if row.provider == provider and row.date < earliest]
    return previous + current


def _bars_for(instrument_id, days):
    """Prioriza Técnico y conserva los cierres anteriores del mismo proveedor."""
    technical = list(TechnicalClose.objects.filter(instrument_id=instrument_id).order_by("date"))
    legacy = list(DailyClose.objects.filter(instrument_id=instrument_id).order_by("date"))
    return _bars_from_rows(_merge_bars(technical, legacy), days)


def _bulk_bars_for(instrument_ids, days):
    ids = list(instrument_ids)
    technical, legacy = defaultdict(list), defaultdict(list)
    for row in TechnicalClose.objects.filter(instrument_id__in=ids).order_by("instrument_id", "date"):
        technical[row.instrument_id].append(row)
    for row in DailyClose.objects.filter(instrument_id__in=ids).order_by("instrument_id", "date"):
        legacy[row.instrument_id].append(row)
    return {ident: _bars_from_rows(_merge_bars(technical.get(ident, []), legacy.get(ident, [])), days) for ident in ids}


def _freshness(bars, provider):
    last = bars[-1]["date"]
    age = (timezone.now().date() - last).days
    return {
        "provider": provider,
        "last_bar": last,
        "age_days": age,
        "stale": age > STALE_DAYS,
        "note": DELAY_NOTE,
    }


def _line(dates, values, digits=4):
    return [
        {"time": d.isoformat(), "value": round(v, digits)}
        for d, v in zip(dates, values, strict=True)
        if v is not None
    ]


def _find_instrument(ticker, instrument_id=None):
    idents = InstrumentIdentifier.objects.filter(kind="TICKER", value__iexact=ticker).select_related(
        "instrument"
    )
    if instrument_id is not None:
        if not str(instrument_id).isdigit():
            raise serializers.ValidationError({"instrument_id": "Identificador inválido."})
        idents = idents.filter(instrument_id=instrument_id)
    ids = list(idents.order_by().values_list("instrument_id", flat=True).distinct()[:2])
    if len(ids) > 1:
        raise serializers.ValidationError(
            {"ticker": "El símbolo corresponde a varias emisoras; indica el instrumento."}
        )
    return idents.filter(instrument_id=ids[0]).first().instrument if ids else None


class SymbolsView(APIView):
    def get(self, request):
        held = _held_instrument_ids(request.user)

        def summaries(model):
            return {
                row["instrument_id"]: row
                for row in model.objects.values("instrument_id").annotate(
                    bars=Count("id"), last_bar=Max("date"), has_ohlc=Count("id", filter=Q(high__isnull=False))
                )
            }

        legacy, technical = summaries(DailyClose), summaries(TechnicalClose)
        out = []
        for ident in (
            InstrumentIdentifier.objects.filter(kind="TICKER").select_related("instrument").order_by("value")
        ):
            data = technical.get(ident.instrument_id) or legacy.get(ident.instrument_id) or {}
            out.append({"instrument_id": ident.instrument_id, "currency": ident.instrument.currency, "market": ident.instrument.market, "ticker": ident.value, "name": ident.instrument.name, "held": ident.instrument_id in held, "bars": data.get("bars", 0),
                        "last_bar": data.get("last_bar"), "has_ohlc": bool(data.get("has_ohlc"))})  # fmt: skip
        return Response(plain(out))


class AnalyzeView(APIView):
    """Velas, series de indicadores listas para graficar, señales con su regla y niveles de un ticker."""

    def get(self, request):
        ticker = str(request.query_params.get("ticker", "")).strip().upper()
        if not ticker:
            raise serializers.ValidationError({"ticker": "Indica un ticker."})
        try:
            days = int(request.query_params.get("days", DEFAULT_DAYS))
        except ValueError:
            raise serializers.ValidationError({"days": "Entero."}) from None
        if not 30 <= days <= MAX_DAYS:
            raise serializers.ValidationError({"days": f"Debe estar entre 30 y {MAX_DAYS}."})
        instrument = _find_instrument(ticker, request.query_params.get("instrument_id"))
        if instrument is None:
            raise serializers.ValidationError({"ticker": "Emisora no encontrada."})
        bars, provider = _bars_for(instrument.id, days)
        if len(bars) < 2:
            return Response({"ticker": ticker, "evaluable": False,
                             "note": "Sin historial guardado: ejecuta refresh_price_history."})  # fmt: skip
        overrides = {k: v for k, v in request.query_params.items() if k in DEFAULTS}
        try:
            result = analyze(bars, overrides)
        except SignalError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc

        dates = [b["date"] for b in bars]
        closes = [b["close"] for b in bars]
        has_ohlc = all(b["high"] is not None for b in bars)
        p = result["params"]
        highs = [b["high"] for b in bars] if has_ohlc else None
        lows = [b["low"] for b in bars] if has_ohlc else None
        macd = ind.macd(closes)
        boll = ind.bollinger(closes)
        vols = [b["volume"] for b in bars]
        series = {
            "sma": _line(dates, ind.sma(closes, p["ma_days"])),
            "ema_fast": _line(dates, ind.ema(closes, p["ema_fast"])),
            "ema_slow": _line(dates, ind.ema(closes, p["ema_slow"])),
            "bollinger_upper": _line(dates, boll["upper"]),
            "bollinger_lower": _line(dates, boll["lower"]),
            "rsi": _line(dates, ind.rsi(closes, p["rsi_days"]), 2),
            "macd": _line(dates, macd["macd"]),
            "macd_signal": _line(dates, macd["signal"]),
            "macd_histogram": _line(dates, macd["histogram"]),
            "atr": _line(dates, ind.atr(highs, lows, closes, p["atr_days"])) if has_ohlc else [],
            "rvol": _line(dates, ind.relative_volume(vols, p["rvol_days"]), 2)
            if all(v is not None for v in vols)
            else [],
        }
        candles = [
            {
                "time": b["date"].isoformat(),
                "open": b["open"],
                "high": b["high"],
                "low": b["low"],
                "close": b["close"],
                "volume": b["volume"],
            }
            for b in bars
        ]
        return Response(plain({"ticker": ticker, "evaluable": True, "has_ohlc": has_ohlc, "freshness": _freshness(bars, provider),
                               "candles": candles, "series": series, **result}))  # fmt: skip


class ScanView(APIView):
    """Señales disparadas hoy en las emisoras que tienes (solo las que tienen historial)."""

    def get(self, request):
        rows = []
        for h in load_holdings(request.user)["holdings"]:
            if h.asset_class == "CASH" or not h.instrument_id:
                continue
            bars, provider = _bars_for(h.instrument_id, DEFAULT_DAYS)
            if len(bars) < 2:
                rows.append({"ticker": h.ticker, "evaluable": False, "note": "Sin historial."})
                continue
            res = analyze(bars)
            rows.append(
                {
                    "ticker": h.ticker, "evaluable": True, "freshness": _freshness(bars, provider),
                    "fired": [s["id"] for s in res["signals"] if s["fired"] is True],
                    "not_evaluable": [s["id"] for s in res["signals"] if s["fired"] is None],
                    "rsi": res["context"].get("rsi"), "last_close": res["context"]["last_close"],
                }
            )  # fmt: skip
        return Response(
            plain(
                {"rows": rows, "note": "Eventos reproducibles sobre cierres diarios; no son recomendaciones."}
            )
        )


def _dec(data, key, default=None, required=False):
    v = data.get(key)
    if v in (None, ""):
        if required:
            raise serializers.ValidationError({key: "Requerido."})
        return default
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise serializers.ValidationError({key: "Número inválido."}) from None
    if not d.is_finite() or abs(d) > Decimal("1e12"):
        raise serializers.ValidationError({key: "Número inválido."})
    return d


class TradePlanView(APIView):
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        raw_limits = data.get("limits") or {}
        if not isinstance(raw_limits, dict) or set(raw_limits) - set(trade_plan.DEFAULT_LIMITS):
            raise serializers.ValidationError(
                {"limits": f"Claves permitidas: {', '.join(trade_plan.DEFAULT_LIMITS)}."}
            )
        limits = {k: _dec(raw_limits, k) for k in raw_limits if raw_limits[k] not in (None, "")}
        try:
            scale = int(data.get("shares_scale", 0))
        except (TypeError, ValueError):
            raise serializers.ValidationError({"shares_scale": "Entero."}) from None
        try:
            result = trade_plan.evaluate(
                _dec(data, "entry", required=True), _dec(data, "stop", required=True), _dec(data, "target", required=True),
                _dec(data, "capital", required=True), _dec(data, "risk_budget"), str(data.get("side", "BUY")), scale,
                _dec(data, "slippage_pct", Decimal(0)), _dec(data, "commission_per_trade", Decimal(0)),
                _dec(data, "daily_loss_so_far", Decimal(0)), limits,
            )  # fmt: skip
        except trade_plan.PlanError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(plain({**result, "computed_at": timezone.now()}))
