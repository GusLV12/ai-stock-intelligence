from django.conf import settings
from django.db import models


class QuantUniverseMember(models.Model):
    """Pertenencia explícita al universo predeterminado del laboratorio."""

    instrument = models.OneToOneField(
        "instruments.Instrument", on_delete=models.PROTECT, related_name="quant_membership"
    )
    created_at = models.DateTimeField(auto_now_add=True)


class QuantExperiment(models.Model):
    """Experimento del laboratorio cuantitativo, INMUTABLE: guarda configuración, huella de los datos, métricas,
    veredicto de promoción y puntajes más recientes. Para repetirlo se crea otro; así queda el historial completo de
    intentos (necesario para corregir por pruebas múltiples) y no se puede maquillar un resultado a posteriori."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quant_experiments"
    )
    name = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)
    horizon = models.PositiveSmallIntegerField()
    config = models.JSONField()  # incluye as_of e instrumentos usados: con eso se reproduce
    dataset_hash = models.CharField(max_length=64, db_index=True)
    result_hash = models.CharField(max_length=64)
    quant_version = models.CharField(max_length=20)
    candidate = models.CharField(max_length=20)
    verdict = models.CharField(max_length=30)
    trials = models.PositiveIntegerField(default=1)  # intentos sobre estos mismos datos, contando este
    result = models.JSONField()

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise models.ProtectedError("Los experimentos son inmutables; crea uno nuevo.", {self})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("Los experimentos no se borran.", {self})
