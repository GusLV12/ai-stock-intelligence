from django.db import models
from django.db.models import Q

from modules.instruments.models import Instrument


class DailyClose(models.Model):
    """Cierre diario de un instrumento según un proveedor. Es el precio de cierre que reporta el proveedor
    (ajustado por splits según él, SIN reinversión de dividendos): sirve para volatilidad, correlaciones y
    gráficas, no para calcular rendimiento total. Idempotente por (instrumento, fecha, proveedor)."""

    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="daily_closes")
    date = models.DateField()
    close = models.DecimalField(max_digits=38, decimal_places=10)
    # Vela completa (opcional): si el proveedor no la dio o era incoherente, solo hay cierre.
    open = models.DecimalField(max_digits=38, decimal_places=10, null=True, blank=True)
    high = models.DecimalField(max_digits=38, decimal_places=10, null=True, blank=True)
    low = models.DecimalField(max_digits=38, decimal_places=10, null=True, blank=True)
    volume = models.BigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3)
    provider = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["instrument_id", "date"]
        constraints = [
            models.UniqueConstraint(fields=["instrument", "date", "provider"], name="uniq_close_per_day_provider"),
            models.CheckConstraint(condition=Q(close__gt=0), name="close_positive"),
        ]
        indexes = [models.Index(fields=["instrument", "date"])]
