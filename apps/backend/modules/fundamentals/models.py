from django.db import models
from django.db.models import Q

from modules.instruments.models import Instrument

MONEY = {"max_digits": 38, "decimal_places": 6, "null": True, "blank": True}

# Campos numéricos del año fiscal. Todos opcionales: un dato ausente es "no disponible", nunca cero.
NUMERIC_FIELDS = [
    "revenue", "gross_profit", "operating_income", "net_income", "cfo", "capex", "ebitda",
    "total_assets", "current_assets", "current_liabilities", "long_term_debt", "total_debt", "cash",
    "shares_outstanding",
]  # fmt: skip


class FinancialYear(models.Model):
    """Estados financieros ANUALES de un instrumento, con versión: una reexpresión crea una versión nueva
    y conserva la anterior (plan 4.3). El periodo económico (period_end) se separa de la fecha en que el
    dato fue público (published_at) para poder evitar el sesgo de anticipación en backtests."""

    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="financial_years")
    fiscal_year = models.PositiveSmallIntegerField()
    version = models.PositiveSmallIntegerField(default=1)
    period_end = models.DateField()
    published_at = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    source = models.CharField(max_length=30, default="MANUAL")  # MANUAL, CSV, SEC_EDGAR…
    revenue = models.DecimalField(**MONEY)
    gross_profit = models.DecimalField(**MONEY)
    operating_income = models.DecimalField(**MONEY)
    net_income = models.DecimalField(**MONEY)
    cfo = models.DecimalField(**MONEY)  # flujo de efectivo operativo
    capex = models.DecimalField(**MONEY)  # inversión en activos fijos, como importe positivo
    ebitda = models.DecimalField(**MONEY)
    total_assets = models.DecimalField(**MONEY)
    current_assets = models.DecimalField(**MONEY)
    current_liabilities = models.DecimalField(**MONEY)
    long_term_debt = models.DecimalField(**MONEY)
    total_debt = models.DecimalField(**MONEY)
    cash = models.DecimalField(**MONEY)
    shares_outstanding = models.DecimalField(**MONEY)  # acciones diluidas al cierre del año
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["instrument_id", "-fiscal_year", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["instrument", "fiscal_year", "version"], name="uniq_financial_year_version"),
            models.CheckConstraint(condition=Q(fiscal_year__gte=1990), name="fy_min"),
        ]

    @property
    def free_cash_flow(self):
        return None if self.cfo is None or self.capex is None else self.cfo - self.capex

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("Los datos financieros no se borran; una corrección crea una versión nueva.", {self})
