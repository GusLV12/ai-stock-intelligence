from django.db import models
from django.db.models import Q

from modules.instruments.models import Instrument

MONEY = {"max_digits": 38, "decimal_places": 10}


class MarketPrice(models.Model):
    """Precio de un instrumento con su procedencia. Nunca se asume tiempo real: cada precio
    conserva proveedor y momento de observación para poder mostrar su antigüedad."""

    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="prices")
    price = models.DecimalField(**MONEY)
    currency = models.CharField(max_length=3)
    provider = models.CharField(max_length=40)  # GBM_REPORT, MANUAL, o un proveedor futuro
    observed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-observed_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["instrument", "provider", "observed_at"], name="uniq_price_per_obs"
            ),
            models.CheckConstraint(condition=Q(price__gte=0), name="price_nonneg"),
        ]


class FxRate(models.Model):
    """Tipo de cambio de REFERENCIA (no el que aplica el broker)."""

    base = models.CharField(max_length=3)
    quote = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=20, decimal_places=10)
    provider = models.CharField(max_length=40)
    observed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-observed_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["base", "quote", "provider", "observed_at"], name="uniq_fx_per_obs"
            ),
            models.CheckConstraint(condition=Q(rate__gt=0), name="fx_positive"),
        ]
