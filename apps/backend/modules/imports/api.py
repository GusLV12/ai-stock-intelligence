from datetime import datetime
from decimal import Decimal

from django.http import HttpResponse
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.accounts.models import InvestmentAccount

from .exports import export_csv, export_json
from .models import ImportBatch, PositionSnapshot
from .services import ImportError_, confirm_import, preview_import
from .snapshots import apply_opening_balances, import_snapshot, reconcile

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
            {
                "row": r.row_number,
                "ticker": r.raw.get("ticker", ""),
                "status": r.status,
                "errors": r.errors,
                "data": r.normalized or r.raw,
            }
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
            resume = request.data.get("resume_preview") == "true"
            batch = preview_import(acc, upload.read(), upload.name or "", resume_preview=resume)
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
        raise NotFound("Lote no encontrado.")
    return batch


def _rec_data(rows):
    return [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in rows]


def _snapshot_data(snap: PositionSnapshot, rows):
    summary = {}
    for r in rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    return {
        "id": snap.id,
        "account": snap.account_id,
        "as_of": snap.as_of,
        "cash": str(snap.cash_amount) if snap.cash_amount is not None else None,
        "opening_applied_at": snap.opening_applied_at,
        "summary": summary,
        "reconciliation": _rec_data(rows),
    }


def _get_snapshot(request, pk):
    snap = PositionSnapshot.objects.filter(pk=pk, account__owner=request.user).first()
    if snap is None:
        raise NotFound("Reporte no encontrado.")
    return snap


class PositionsImportView(APIView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        acc = _own_account(request, request.data.get("account"))
        upload = request.FILES.get("file")
        if upload is None:
            raise serializers.ValidationError({"file": "Adjunta el XLSX de portafolio."})
        if upload.size > MAX_UPLOAD_BYTES:
            raise serializers.ValidationError({"file": "El archivo supera 5 MB."})
        as_of = None
        if raw := request.data.get("as_of"):
            try:
                as_of = datetime.fromisoformat(raw)
            except ValueError as exc:
                raise serializers.ValidationError({"as_of": "Fecha ISO 8601 inválida."}) from exc
        try:
            snap = import_snapshot(acc, upload.read(), upload.name or "", as_of)
        except ImportError_ as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(_snapshot_data(snap, reconcile(snap)), status=status.HTTP_201_CREATED)


class SnapshotReconciliationView(APIView):
    def get(self, request, pk):
        snap = _get_snapshot(request, pk)
        return Response(_snapshot_data(snap, reconcile(snap)))


class SnapshotOpeningView(APIView):
    def post(self, request, pk):
        snap = _get_snapshot(request, pk)
        try:
            result = apply_opening_balances(snap)
        except (ImportError_, ValueError) as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        snap.refresh_from_db()
        return Response({"result": result, **_snapshot_data(snap, reconcile(snap))})
