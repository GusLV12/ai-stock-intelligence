from django.conf import settings
from django.db import models

from modules.instruments.models import Instrument


class Valuation(models.Model):
    """Instantánea inmutable de una valoración: supuestos de entrada, resultado, versión del modelo y fecha.
    Permite reproducirla y compararla después; no se edita ni se borra (se crea una nueva)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="valuations")
    instrument = models.ForeignKey(Instrument, null=True, blank=True, on_delete=models.PROTECT)
    label = models.CharField(max_length=120, blank=True)
    method = models.CharField(max_length=30, default="asi_fair_value_v1")
    model_version = models.CharField(max_length=20)
    inputs = models.JSONField()
    output = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("Las valoraciones son instantáneas inmutables.", {self})
