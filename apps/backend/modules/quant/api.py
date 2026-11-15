import math
from dataclasses import asdict

from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from modules.valuations.fair_value import plain

from . import promotion, worker
from .features import FEATURES
from .models import QuantExperiment
from .runner import (
    ALL_MODELS,
    DEFAULT_FOLDS,
    DEFAULT_MODELS,
    HEAVY_MODELS,
    HORIZONS,
    MAX_FOLDS,
    MODEL_LABELS,
    ExperimentError,
)
from .services import MIN_BARS, create_experiment, load_series, verify

DISCLAIMER = (
    "Laboratorio de investigación: mide si una señal habría tenido capacidad predictiva con datos pasados. "
    "No es una recomendación de inversión ni autoriza operar; un resultado positivo solo significa 'seguir observando'."
)


class QuantThrottle(UserRateThrottle):
    scope = "quant"


def _summary(e: QuantExperiment) -> dict:
    cand = e.result["models"][e.candidate]
    return {
        "id": e.id, "name": e.name, "created_at": e.created_at, "horizon": e.horizon, "verdict": e.verdict, "candidate": e.candidate,
        "candidate_label": cand["label"], "test_rank_ic": cand["test"]["rank_ic"]["mean"], "trials": e.trials,
        "dataset_hash": e.dataset_hash, "universe": len(e.result["meta"]["universe"]),
    }  # fmt: skip


def _detail(e: QuantExperiment) -> dict:
    return {
        **_summary(e),
        "config": e.config,
        "result_hash": e.result_hash,
        "quant_version": e.quant_version,
        "result": e.result,
        "disclaimer": DISCLAIMER,
    }


def _num(data, key, default, lo, hi, integer=False):
    raw = data.get(key, default)
    try:
        v = int(raw) if integer else float(raw)
    except (TypeError, ValueError):
        raise serializers.ValidationError({key: "Debe ser un número."}) from None
    if isinstance(raw, bool) or not math.isfinite(v) or not lo <= v <= hi:
        raise serializers.ValidationError({key: f"Debe estar entre {lo} y {hi}."})
    return v


class OverviewView(APIView):
    def get(self, request):
        series, tickers = load_series()
        last = max((b["date"] for bars in series.values() for b in bars), default=None)
        return Response(
            plain(
                {
                    "universe": [{"instrument": i, "ticker": tickers.get(i, str(i)), "bars": len(b), "last": b[-1]["date"]} for i, b in sorted(series.items())],
                    "min_bars": MIN_BARS, "last_bar": last, "lightgbm_available": worker.available(), "qlib_available": worker.qlib_available(), "horizons": list(HORIZONS), "default_folds": DEFAULT_FOLDS, "max_folds": MAX_FOLDS,
                    "models": [{"id": m, "label": MODEL_LABELS[m], "default": m in DEFAULT_MODELS, "heavy": m in HEAVY_MODELS} for m in ALL_MODELS], "features": FEATURES,
                    "criteria": asdict(promotion.Criteria()), "disclaimer": DISCLAIMER,
                }
            )
        )  # fmt: skip


class ExperimentsView(APIView):
    throttle_classes = [QuantThrottle]

    def get(self, request):
        return Response(plain([_summary(e) for e in QuantExperiment.objects.filter(user=request.user)[:100]]))

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        horizon = _num(data, "horizon", 20, 1, 250, integer=True)
        if horizon not in HORIZONS:
            raise serializers.ValidationError({"horizon": f"Debe ser uno de {list(HORIZONS)}."})
        models = data.get("models", DEFAULT_MODELS)
        if (
            not isinstance(models, list)
            or not models
            or any(m not in MODEL_LABELS for m in models)
            or len(set(models)) != len(models)
        ):
            raise serializers.ValidationError(
                {"models": f"Lista sin repetidos de: {', '.join(MODEL_LABELS)}."}
            )
        costs = data.get("costs", {})
        if not isinstance(costs, dict):
            raise serializers.ValidationError({"costs": "Debe ser un objeto con puntos base."})
        config = {
            "horizon": horizon, "models": models, "seed": _num(data, "seed", 0, 0, 2**31 - 1, integer=True),
            "top_k": _num(data, "top_k", 3, 1, 20, integer=True), "rebalance_days": _num(data, "rebalance_days", horizon, 1, 120, integer=True),
            "folds": _num(data, "folds", DEFAULT_FOLDS, 1, MAX_FOLDS, integer=True), "delay": 1, "capital": _num(data, "capital", 100_000, 1_000, 1e9),
            "costs": {k: _num(costs, k, d, 0, 200) for k, d in (("commission_bps", 1), ("half_spread_bps", 2), ("slippage_bps", 2))},
        }  # fmt: skip
        name = str(data.get("name") or "").strip()[:80] or f"Experimento h={horizon}"
        try:
            exp = create_experiment(request.user, name, config)
        except (ValueError, ExperimentError) as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(plain(_detail(exp)), status=status.HTTP_201_CREATED)


def _mine(request, pk) -> QuantExperiment:
    e = QuantExperiment.objects.filter(user=request.user, pk=pk).first()
    if e is None:
        raise NotFound("Experimento no encontrado.")
    return e


class ExperimentDetailView(APIView):
    def get(self, request, pk):
        return Response(plain(_detail(_mine(request, pk))))


class VerifyView(APIView):
    throttle_classes = [QuantThrottle]

    def post(self, request, pk):
        return Response(plain(verify(_mine(request, pk))))
