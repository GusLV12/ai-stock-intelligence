from django.db import models


class Instrument(models.Model):
    """Instrumento con ID interno. Se separa por mercado y emisor, no por ticker aislado."""

    class AssetClass(models.TextChoices):
        STOCK = "STOCK", "Acción"
        ETF = "ETF", "ETF"
        ADR = "ADR", "ADR"
        CRYPTO = "CRYPTO", "Criptomoneda"

    asset_class = models.CharField(max_length=10, choices=AssetClass.choices)
    name = models.CharField(max_length=200)
    market = models.CharField(max_length=30, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    # Decimales máximos permitidos en cantidades (acciones fraccionarias: 6; cripto: 8–18).
    quantity_scale = models.PositiveSmallIntegerField(default=6)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class InstrumentIdentifier(models.Model):
    class Kind(models.TextChoices):
        TICKER = "TICKER", "Ticker"
        ISIN = "ISIN", "ISIN"
        CUSIP = "CUSIP", "CUSIP"
        PROVIDER = "PROVIDER", "ID de proveedor"

    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="identifiers")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    value = models.CharField(max_length=50)
    # Tickers pueden repetirse entre mercados; se distingue por el mercado del instrumento.

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["instrument", "kind", "value"], name="uniq_identifier_per_instrument"
            )
        ]
