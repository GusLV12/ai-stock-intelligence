from django.http import HttpResponse
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.instruments.models import Instrument

from .models import NUMERIC_FIELDS, FinancialYear
from .services import CSV_COLUMNS, FundamentalsError, import_financials, parse_csv, parse_xlsx
from .spreadsheet import make_xlsx

MAX_UPLOAD_BYTES = 2 * 1024 * 1024


class ImportView(APIView):
    """JSON: {"rows": [...], "source": "MANUAL"} o multipart con `file` (CSV). Todo o nada."""

    parser_classes = [JSONParser, MultiPartParser]

    def get(self, request):
        if request.query_params.get("format") != "xlsx":
            raise serializers.ValidationError({"format": "Usa xlsx para descargar la plantilla."})
        example = {"ticker": "AAA", "fiscal_year": 2025, "period_end": "2025-12-31",
                   "published_at": "2026-02-20", "currency": "USD", "revenue": 1200,
                   "gross_profit": 400, "net_income": 100, "cfo": 150, "capex": 30,
                   "total_assets": 1100, "current_assets": 500, "current_liabilities": 300,
                   "long_term_debt": 250, "total_debt": 300, "cash": 80, "shares_outstanding": 100}
        content = make_xlsx([CSV_COLUMNS, [example.get(col) for col in CSV_COLUMNS]], "Estados financieros")
        response = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = 'attachment; filename="plantilla-estados-financieros.xlsx"'
        return response

    def post(self, request):
        try:
            upload = request.FILES.get("file")
            if upload is not None:
                if upload.size > MAX_UPLOAD_BYTES:
                    raise serializers.ValidationError({"file": "El archivo supera 2 MB."})
                filename = (upload.name or "").lower()
                if filename.endswith(".xlsx"):
                    rows, source = parse_xlsx(upload.read()), "XLSX"
                elif filename.endswith(".csv"):
                    rows, source = parse_csv(upload.read()), "CSV"
                else:
                    raise serializers.ValidationError({"file": "Usa un archivo CSV o XLSX."})
            else:
                data = request.data if isinstance(request.data, dict) else {}
                rows, source = data.get("rows"), "MANUAL"
            result = import_financials(rows, source)
        except FundamentalsError as exc:
            raise serializers.ValidationError({"detail": str(exc), "errors": exc.errors}) from exc
        return Response(result, status=status.HTTP_201_CREATED)


class InstrumentFinancialsView(APIView):
    def get(self, request, pk):
        if not Instrument.objects.filter(pk=pk).exists():
            raise NotFound("Instrumento no encontrado.")
        rows = FinancialYear.objects.filter(instrument_id=pk)
        return Response(
            [
                {
                    "fiscal_year": r.fiscal_year,
                    "version": r.version,
                    "period_end": r.period_end,
                    "published_at": r.published_at,
                    "currency": r.currency,
                    "source": r.source,
                    "free_cash_flow": None if r.free_cash_flow is None else str(r.free_cash_flow),
                    **{f: None if getattr(r, f) is None else str(getattr(r, f)) for f in NUMERIC_FIELDS},
                }
                for r in rows
            ]
        )
