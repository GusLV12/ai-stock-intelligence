from django.http import HttpResponse
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.accounts.models import InvestmentAccount

from .exports import export_csv, export_json
from .models import ImportBatch
from .services import ImportError_, confirm_import, preview_import

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _own_account(request, account_id):
    acc = InvestmentAccount.objects.filter(owner=request.user, pk=account_id).first()
    if acc is None:
        raise serializers.ValidationError({"account": "Cuenta no encontrada."})
    return acc


def _batch_data(batch: ImportBatch, with_rows=True):
    data = {
        "id": batch.id,
        "account": batch.account_id,
        "filename": batch.filename,
        "status": batch.status,
        "adapter_version": batch.adapter_version,
        "created_at": batch.created_at,
        "confirmed_at": batch.confirmed_at,
        "summary": {},
    }
    rows = list(batch.rows.all())
    for r in rows:
        data["summary"][r.status] = data["summary"].get(r.status, 0) + 1
    if with_rows:
        data["rows"] = [
            {"row": r.row_number, "status": r.status, "errors": r.errors, "data": r.normalized or r.raw}
            for r in rows
        ]
    return data


class ImportsView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        acc = _own_account(request, request.data.get("account"))
        upload = request.FILES.get("file")
        if upload is None:
            raise serializers.ValidationError({"file": "Adjunta un archivo CSV."})
        if upload.size > MAX_UPLOAD_BYTES:
            raise serializers.ValidationError({"file": "El archivo supera 5 MB."})
        try:
            batch = preview_import(acc, upload.read(), upload.name or "")
        except ImportError_ as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(_batch_data(batch), status=status.HTTP_201_CREATED)


class ImportDetailView(APIView):
    def get(self, request, pk):
        batch = _get_batch(request, pk)
        return Response(_batch_data(batch))


class ImportConfirmView(APIView):
    def post(self, request, pk):
        batch = _get_batch(request, pk)
        try:
            result = confirm_import(batch)
        except (ImportError_, ValueError) as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        batch.refresh_from_db()
        return Response({"result": result, "batch": _batch_data(batch, with_rows=False)})


class ExportView(APIView):
    def get(self, request):
        acc = _own_account(request, request.query_params.get("account"))
        fmt = request.query_params.get("format", "csv")
        if fmt == "json":
            return HttpResponse(export_json(acc), content_type="application/json; charset=utf-8")
        if fmt == "csv":
            resp = HttpResponse(export_csv(acc), content_type="text/csv; charset=utf-8")
            resp["Content-Disposition"] = 'attachment; filename="movimientos.csv"'
            return resp
        raise serializers.ValidationError({"format": "Usa csv o json."})


def _get_batch(request, pk):
    batch = ImportBatch.objects.filter(pk=pk, account__owner=request.user).first()
    if batch is None:
        from rest_framework.exceptions import NotFound

        raise NotFound("Lote no encontrado.")
    return batch
