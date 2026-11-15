from django.conf import settings
from django.db import models


class PortfolioPolicy(models.Model):
    """Restricciones y objetivo del usuario. Cada versión se conserva; la activa es la más reciente."""

    class Objective(models.TextChoices):
        GROWTH = "growth", "Crecimiento"
        DIVIDENDS = "dividends", "Dividendos"
        MIXED = "mixed", "Mixta"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="portfolio_policies"
    )
    name = models.CharField(max_length=100)
    objective = models.CharField(max_length=10, choices=Objective.choices, default=Objective.MIXED)
    max_position_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    max_class_pct = models.JSONField(default=dict, blank=True)  # {"CRYPTO": 10, "ETF": 30}
    min_cash_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    target_weights = models.JSONField(
        default=dict, blank=True
    )  # {"AAA": 20, "BBB": 15}; el resto queda en efectivo
    horizon_years = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("Las políticas se versionan; crea una nueva.", {self})

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "objective": self.objective,
            "max_position_pct": self.max_position_pct, "max_class_pct": self.max_class_pct or {},
            "min_cash_pct": self.min_cash_pct, "target_weights": self.target_weights or {},
            "horizon_years": self.horizon_years,
        }  # fmt: skip


def active_policy(user):
    return PortfolioPolicy.objects.filter(user=user).first()


from .thesis_models import InvestmentThesis, ThesisFollowUp  # noqa: F401  (registra los modelos)
