from django.conf import settings
from django.db import models

from modules.instruments.models import Instrument


class PriceAlert(models.Model):
    """Alerta de UN solo disparo sobre precio o señal técnica. No envía mensajes a ningún lado: al cumplirse queda
    marcada con los datos exactos (precio, proveedor, antigüedad) que la dispararon, para poder auditarla."""

    class Kind(models.TextChoices):
        PRICE_ABOVE = "PRICE_ABOVE", "Precio sobre"
        PRICE_BELOW = "PRICE_BELOW", "Precio bajo"
        SIGNAL = "SIGNAL", "Señal técnica"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="price_alerts")
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    threshold = models.DecimalField(max_digits=38, decimal_places=10, null=True, blank=True)
    signal_id = models.CharField(max_length=30, blank=True)
    note = models.CharField(max_length=200, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    triggered_at = models.DateTimeField(null=True, blank=True)
    triggered_detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
