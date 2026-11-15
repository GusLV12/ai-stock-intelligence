from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.instruments.models import Instrument

from .models import NUMERIC_FIELDS, FinancialYear
from .services import FundamentalsError, import_financials, parse_csv

MAX_UPLOAD_BYTES = 2 * 1024 * 1024


class ImportView(APIView):
    """JSON: {"rows": [...], "source": "MANUAL"} o multipart con `file` (CSV). Todo o nada."""

    parser_classes = [JSONParser, MultiPartParser]

    def post(self, request):
        try:
            upload = request.FILES.get("file")
            if upload is not None:
                if upload.size > MAX_UPLOAD_BYTES:
                    raise serializers.ValidationError({"file": "El archivo supera 2 MB."})
                rows, source = parse_csv(upload.read()), "CSV"
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
