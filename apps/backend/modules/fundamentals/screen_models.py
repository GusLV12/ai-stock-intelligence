from django.conf import settings
from django.db import models


class ScreenRun(models.Model):
    """Corrida inmutable del screener: filtros, cobertura y resultados completos con la referencia de cada dato.
    Permite reproducir y auditar por qué una empresa pasó o no."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="screen_runs")
    model_version = models.CharField(max_length=20)
    params = models.JSONField()
    coverage = models.JSONField()
    results = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("Las corridas del screener son instantáneas inmutables.", {self})
