from django.conf import settings
from django.db import models


class InvestmentAccount(models.Model):
    """Cuenta real (GBM USA, Mercado Pago) o cartera de prueba."""

    class Broker(models.TextChoices):
        GBM_USA = "GBM_USA", "GBM Trading USA"
        MERCADO_PAGO = "MERCADO_PAGO", "Mercado Pago (cripto)"
        PAPER = "PAPER", "Cartera simulada"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="investment_accounts"
    )
    name = models.CharField(max_length=100)
    broker = models.CharField(max_length=20, choices=Broker.choices)
    base_currency = models.CharField(max_length=3, default="USD")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="uniq_account_name_per_owner")
        ]

    def __str__(self):
        return f"{self.name} ({self.broker})"
