from django.db import models


class ProviderQuota(models.Model):
    """Contador compartido por procesos, sin almacenar claves ni consultas."""

    provider = models.CharField(max_length=40)
    minute = models.BigIntegerField()
    used = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "minute"], name="unique_provider_quota_minute")
        ]
