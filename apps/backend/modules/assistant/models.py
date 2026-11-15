from django.conf import settings
from django.db import models


class AssistantLog(models.Model):
    """Registro de cada consulta: qué se preguntó (truncado), qué herramientas se usaron y cuántas
    cifras no pudieron verificarse. No guarda las respuestas ni los datos financieros devueltos."""

    class Status(models.TextChoices):
        OK = "ok", "ok"
        UNAVAILABLE = "unavailable", "modelo no disponible"
        ERROR = "error", "error"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_logs"
    )
    question = models.CharField(max_length=300)
    model = models.CharField(max_length=60)
    tools = models.JSONField(default=list)
    unverified_count = models.PositiveIntegerField(default=0)
    steps = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=12, choices=Status.choices)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
