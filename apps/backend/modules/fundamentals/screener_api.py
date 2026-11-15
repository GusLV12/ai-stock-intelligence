import csv
import io

from django.http import HttpResponse
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from .screen_models import ScreenRun
from .screener import MODEL_VERSION, ScreenError, run_screen

CSV_COLUMNS = [
    "ticker", "aprobada", "filtros_fallidos", "filtros_sin_dato", "fscore", "fscore_estado", "margen_de_seguridad",
    "fcf", "deuda_sobre_activos", "crecimiento_ventas", "valoracion", "calidad", "crecimiento", "riesgo",
    "calidad_de_datos", "anio_fiscal", "version_datos",
]  # fmt: skip


def _safe(value) -> str:
    """Neutraliza textos que una hoja de cálculo interpretaría como fórmula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "@") or (text[:1] == "-" and not text[1:2].isdigit()) else text


def to_csv(results: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for r in results["rows"]:
        m, rt = r["metrics"], r["ratings"]
        w.writerow(
            [
                _safe(r["ticker"]), "si" if r["passed"] else "no", ";".join(r["failed_filters"]),
                ";".join(r["missing_data_filters"]), r["fscore"]["score"], r["fscore"]["status"],
                m["margin_of_safety"], m["free_cash_flow"], m["debt_to_assets"], m["revenue_growth"],
                rt["valuation"]["label"], rt["financial_quality"]["label"], rt["growth"]["label"], rt["risk"]["label"],
                rt["data_quality"]["label"], r["data_refs"]["fiscal_year"], r["data_refs"]["version"],
            ]
        )  # fmt: skip
    return out.getvalue()


class RunScreenView(APIView):
    def post(self, request):
        params = dict(request.data) if isinstance(request.data, dict) else {}
        try:
            results = run_screen(request.user, params)
        except ScreenError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise serializers.ValidationError({"detail": "Filtros con formato inválido."}) from exc
        run = ScreenRun.objects.create(
            user=request.user, model_version=MODEL_VERSION, params=results["params"],
            coverage=results["coverage"], results=results,
        )  # fmt: skip
        return Response({"id": run.id, "created_at": run.created_at, **results}, status=status.HTTP_201_CREATED)


class RunListView(APIView):
    def get(self, request):
        return Response(
            [
                {"id": r.id, "created_at": r.created_at, "model_version": r.model_version, "params": r.params,
                 "coverage": r.coverage, "candidates": r.results.get("candidates", [])}
                for r in ScreenRun.objects.filter(user=request.user)[:50]
            ]
        )


class RunDetailView(APIView):
    def get(self, request, pk):
        run = ScreenRun.objects.filter(pk=pk, user=request.user).first()
        if run is None:
            raise NotFound("Corrida no encontrada.")
        fmt = request.query_params.get("format", "json")
        if fmt == "csv":
            resp = HttpResponse(to_csv(run.results), content_type="text/csv; charset=utf-8")
            resp["Content-Disposition"] = f'attachment; filename="screener-{run.id}.csv"'
            return resp
        if fmt != "json":
            raise serializers.ValidationError({"format": "Usa json o csv."})
        return Response({"id": run.id, "created_at": run.created_at, **run.results})
