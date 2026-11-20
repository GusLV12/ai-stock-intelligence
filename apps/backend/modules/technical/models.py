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


class ChartDrawing(models.Model):
    """Anotación del usuario, separada de los niveles calculados automáticamente."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chart_drawings"
    )
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="chart_drawings")
    kind = models.CharField(max_length=12, choices=[("horizontal", "Horizontal"), ("trend", "Tendencia")])
    points = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class WatchlistEntry(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="watchlist")
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="watchers")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "instrument"], name="unique_watchlist_user_instrument")
        ]


class TechnicalClose(models.Model):
    """Cierre aislado de la descarga manual de la terminal."""

    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="technical_closes")
    date = models.DateField()
    close = models.DecimalField(max_digits=38, decimal_places=10)
    open = models.DecimalField(max_digits=38, decimal_places=10, null=True, blank=True)
    high = models.DecimalField(max_digits=38, decimal_places=10, null=True, blank=True)
    low = models.DecimalField(max_digits=38, decimal_places=10, null=True, blank=True)
    volume = models.BigIntegerField(null=True, blank=True)
    adj_close = models.DecimalField(max_digits=38, decimal_places=10, null=True, blank=True)
    currency = models.CharField(max_length=3)
    provider = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["instrument_id", "date"]
        constraints = [
            models.UniqueConstraint(fields=["instrument", "date", "provider"], name="uniq_technical_close"),
            models.CheckConstraint(condition=models.Q(close__gt=0), name="technical_close_positive"),
        ]
        indexes = [models.Index(fields=["instrument", "date"])]


class MarketIdentity(models.Model):
    """Identidad confirmada para vincular bolsas equivalentes entre proveedores."""

    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="market_identities")
    symbol = models.CharField(max_length=30)
    market = models.CharField(max_length=30)
    currency = models.CharField(max_length=3)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["symbol", "market", "currency"], name="unique_market_identity")
        ]
