from django.conf import settings
from django.db import models

from modules.instruments.models import Instrument


class InvestmentThesis(models.Model):
    """Tesis de inversión INMUTABLE: lo que pensabas, con qué precio y qué la invalidaría (plan 8.6).
    Nada se edita ni se borra: el seguimiento se agrega aparte, para reducir el sesgo retrospectivo."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="theses")
    instrument = models.ForeignKey(Instrument, null=True, blank=True, on_delete=models.PROTECT)
    title = models.CharField(max_length=150)
    strategy = models.CharField(max_length=20, default="long_term")  # long_term | swing | day_trading
    horizon = models.CharField(max_length=60, blank=True)
    thesis = models.TextField(max_length=5000)
    risks = models.TextField(max_length=3000)
    invalidation = models.TextField(max_length=3000)  # qué tendría que pasar para estar equivocado
    price = models.DecimalField(max_digits=38, decimal_places=10, null=True, blank=True)
    price_source = models.JSONField(default=dict, blank=True)  # proveedor y momento del precio observado
    valuation_id = models.PositiveIntegerField(null=True, blank=True)  # Fair Value en que se apoyó
    sources = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("Las tesis son inmutables; agrega un seguimiento.", {self})


class ThesisFollowUp(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Abierta"
        CONFIRMED = "CONFIRMED", "Confirmada"
        INVALIDATED = "INVALIDATED", "Invalidada"
        CLOSED = "CLOSED", "Cerrada"

    thesis = models.ForeignKey(InvestmentThesis, on_delete=models.PROTECT, related_name="follow_ups")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    note = models.TextField(max_length=3000)
    price = models.DecimalField(max_digits=38, decimal_places=10, null=True, blank=True)
    price_source = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("Los seguimientos no se borran.", {self})
