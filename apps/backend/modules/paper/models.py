from django.conf import settings
from django.db import models

MONEY = {"max_digits": 38, "decimal_places": 10}


class PaperAccount(models.Model):
    """Cuenta VIRTUAL de práctica. No está conectada a ningún broker ni al libro contable real: nada de lo que ocurre
    aquí mueve dinero, crea transacciones reales ni afecta tu portafolio, tus lotes o tu capa fiscal."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="paper_accounts"
    )
    name = models.CharField(max_length=80)
    initial_cash = models.DecimalField(**MONEY)
    currency = models.CharField(max_length=3, default="USD")
    config = models.JSONField(
        default=dict
    )  # slippage_bps, commission_bps, commission_min, max_price_age_days, max_position_pct
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def delete(self, *args, **kwargs):
        raise models.ProtectedError(
            "Las cuentas de paper trading no se borran: su historial es evidencia.", {self}
        )


class PaperOrder(models.Model):
    class Side(models.TextChoices):
        BUY = "BUY", "Compra"
        SELL = "SELL", "Venta"

    class Type(models.TextChoices):
        MARKET = "MARKET", "A mercado"
        LIMIT = "LIMIT", "Limitada"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        FILLED = "FILLED", "Ejecutada"
        REJECTED = "REJECTED", "Rechazada"
        CANCELLED = "CANCELLED", "Cancelada"
        EXPIRED = "EXPIRED", "Vencida"

    account = models.ForeignKey(PaperAccount, on_delete=models.PROTECT, related_name="orders")
    instrument = models.ForeignKey("instruments.Instrument", on_delete=models.PROTECT)
    side = models.CharField(max_length=4, choices=Side.choices)
    order_type = models.CharField(max_length=6, choices=Type.choices, default=Type.MARKET)
    quantity = models.DecimalField(**MONEY)
    limit_price = models.DecimalField(null=True, blank=True, **MONEY)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)  # cuándo se ejecutó, rechazó, canceló o venció
    # Precio de referencia con procedencia (el que habría visto el operador al decidir) y precio simulado de ejecución.
    reference_price = models.DecimalField(null=True, blank=True, **MONEY)
    reference_source = models.JSONField(default=dict, blank=True)
    fill_price = models.DecimalField(null=True, blank=True, **MONEY)
    fee = models.DecimalField(default=0, **MONEY)
    adverse_slippage_bps = models.DecimalField(
        null=True, blank=True, max_digits=14, decimal_places=4
    )  # discrepancia vs referencia
    reason = models.CharField(max_length=300, blank=True)  # por qué se rechazó/venció
    note = models.CharField(max_length=300, blank=True)
    source = models.JSONField(default=dict, blank=True)  # p. ej. {"kind": "quant", "experiment": 3}

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            current = PaperOrder.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if current != self.Status.PENDING:
                raise models.ProtectedError("Una orden ya decidida es inmutable.", {self})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("Las órdenes simuladas no se borran.", {self})


class PaperJournalEntry(models.Model):
    """Diario de decisiones: por qué se opera, qué se esperaba y qué se aprendió. Inmutable."""

    class Kind(models.TextChoices):
        DECISION = "DECISION", "Decisión"
        REVIEW = "REVIEW", "Revisión"
        NOTE = "NOTE", "Nota"

    account = models.ForeignKey(PaperAccount, on_delete=models.PROTECT, related_name="journal")
    order = models.ForeignKey(
        PaperOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="journal"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.NOTE)
    text = models.TextField(max_length=3000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise models.ProtectedError("Las entradas del diario son inmutables.", {self})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("Las entradas del diario no se borran.", {self})
