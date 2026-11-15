from django.db import models
from django.db.models import Q

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument

QTY = {"max_digits": 38, "decimal_places": 18}
MONEY = {"max_digits": 38, "decimal_places": 10}


class ImmutableQuerySet(models.QuerySet):
    def delete(self):
        raise models.ProtectedError("Los asientos confirmados no se borran; use una reversa.", set())


class Transaction(models.Model):
    """Evento de inversión. Las correcciones se hacen con una reversa, nunca borrando."""

    class Operation(models.TextChoices):
        BUY = "BUY", "Compra"
        SELL = "SELL", "Venta"

    class Status(models.TextChoices):
        CONFIRMED = "CONFIRMED", "Confirmada"
        REVERSED = "REVERSED", "Revertida"

    account = models.ForeignKey(InvestmentAccount, on_delete=models.PROTECT, related_name="transactions")
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="transactions")
    operation = models.CharField(max_length=10, choices=Operation.choices)
    quantity = models.DecimalField(**QTY)
    price_per_unit = models.DecimalField(**MONEY)
    fees = models.DecimalField(default=0, **MONEY)
    currency = models.CharField(max_length=3)
    executed_at = models.DateTimeField()
    settled_at = models.DateTimeField(null=True, blank=True)
    broker_reference = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CONFIRMED)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        ordering = ["executed_at", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="tx_quantity_positive"),
            models.CheckConstraint(condition=Q(price_per_unit__gte=0), name="tx_price_nonneg"),
            models.CheckConstraint(condition=Q(fees__gte=0), name="tx_fees_nonneg"),
        ]

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("Los asientos confirmados no se borran; use una reversa.", {self})


class TaxLot(models.Model):
    """Lote de adquisición (costo contable). El costo incluye la comisión de compra."""

    transaction = models.OneToOneField(Transaction, on_delete=models.PROTECT, related_name="lot")
    account = models.ForeignKey(InvestmentAccount, on_delete=models.PROTECT)
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT)
    acquired_at = models.DateTimeField()
    quantity_acquired = models.DecimalField(**QTY)
    quantity_remaining = models.DecimalField(**QTY)
    total_cost = models.DecimalField(**MONEY)  # costo del lote completo, con comisión
    currency = models.CharField(max_length=3)

    class Meta:
        ordering = ["acquired_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity_remaining__gte=0)
                & Q(quantity_remaining__lte=models.F("quantity_acquired")),
                name="lot_remaining_in_range",
            )
        ]


class LotDisposal(models.Model):
    """Asignación contable de una venta a un lote (FIFO). Capa fiscal va aparte."""

    sale = models.ForeignKey(Transaction, on_delete=models.PROTECT, related_name="disposals")
    lot = models.ForeignKey(TaxLot, on_delete=models.PROTECT, related_name="disposals")
    quantity = models.DecimalField(**QTY)
    cost_basis = models.DecimalField(**MONEY)
    net_proceeds = models.DecimalField(**MONEY)  # ingreso bruto menos comisión de venta asignada

    @property
    def realized_pnl(self):
        return self.net_proceeds - self.cost_basis
