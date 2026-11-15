from django.db import models

from modules.accounts.models import InvestmentAccount
from modules.portfolio.models import Transaction


class ImportBatch(models.Model):
    """Un archivo cargado. El hash SHA-256 impide importar dos veces el mismo archivo."""

    class Status(models.TextChoices):
        PREVIEW = "PREVIEW", "Vista previa"
        CONFIRMED = "CONFIRMED", "Confirmado"

    account = models.ForeignKey(InvestmentAccount, on_delete=models.PROTECT, related_name="import_batches")
    source = models.CharField(max_length=30, default="CSV_CANONICAL")
    adapter_version = models.CharField(max_length=20, default="1")
    filename = models.CharField(max_length=255, blank=True)
    file_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PREVIEW)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "file_sha256"], name="uniq_file_per_account")
        ]


class ImportRow(models.Model):
    class Status(models.TextChoices):
        VALID = "VALID", "Válida"
        DUPLICATE = "DUPLICATE", "Duplicada"
        MATCH_MANUAL = "MATCH_MANUAL", "Coincide con captura manual"
        INVALID = "INVALID", "Inválida"
        DISCREPANCY = "DISCREPANCY", "Discrepancia"
        CONFIRMED = "CONFIRMED", "Confirmada"
        LINKED = "LINKED", "Vinculada a captura manual"

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="rows")
    row_number = models.PositiveIntegerField()
    raw = models.JSONField()  # fila original, sin modificar
    normalized = models.JSONField(null=True, blank=True)
    fingerprint = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices)
    errors = models.JSONField(default=list)
    transaction = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ["batch_id", "row_number"]
        constraints = [
            models.UniqueConstraint(fields=["batch", "row_number"], name="uniq_row_per_batch")
        ]


class ManualImportMatch(models.Model):
    """Vincula una captura manual con la fila importada que la confirma, sin duplicar la venta."""

    manual_transaction = models.OneToOneField(
        Transaction, on_delete=models.PROTECT, related_name="import_match"
    )
    import_row = models.OneToOneField(ImportRow, on_delete=models.PROTECT, related_name="manual_match")
    matched_at = models.DateTimeField(auto_now_add=True)


class PositionSnapshot(models.Model):
    """Posiciones REPORTADAS por el broker en una fecha. No es historial: sirve para conciliar
    y, solo si el usuario lo confirma, para fijar un saldo de apertura."""

    account = models.ForeignKey(InvestmentAccount, on_delete=models.PROTECT, related_name="snapshots")
    source = models.CharField(max_length=40, default="GBM_USA_PORTFOLIO_XLSX")
    filename = models.CharField(max_length=255, blank=True)
    file_sha256 = models.CharField(max_length=64)
    as_of = models.DateTimeField()
    cash_amount = models.DecimalField(max_digits=38, decimal_places=10, null=True, blank=True)
    cash_currency = models.CharField(max_length=3, default="USD")
    opening_applied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "file_sha256"], name="uniq_snapshot_file_per_account")
        ]


class SnapshotLine(models.Model):
    snapshot = models.ForeignKey(PositionSnapshot, on_delete=models.CASCADE, related_name="lines")
    ticker = models.CharField(max_length=20)
    quantity = models.DecimalField(max_digits=38, decimal_places=18)
    average_cost = models.DecimalField(max_digits=38, decimal_places=10, null=True)
    market_price = models.DecimalField(max_digits=38, decimal_places=10, null=True)
    market_value = models.DecimalField(max_digits=38, decimal_places=10, null=True)
    raw = models.JSONField()

    class Meta:
        ordering = ["ticker"]
        constraints = [
            models.UniqueConstraint(fields=["snapshot", "ticker"], name="uniq_ticker_per_snapshot")
        ]
