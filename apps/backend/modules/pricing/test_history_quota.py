from datetime import date
from decimal import Decimal

import pytest

from modules.instruments.models import Instrument
from modules.pricing.history import refresh_history
from modules.pricing.providers import ProviderError, ProviderRateLimit


@pytest.mark.django_db
def test_lote_reanuda_en_la_misma_emisora_al_renovarse_la_cuota(monkeypatch):
    instruments = {
        str(i): Instrument.objects.create(name=str(i), asset_class="STOCK", currency="USD") for i in range(4)
    }

    class Provider:
        name = "SIMULADO"

        def __init__(self):
            self.calls = []
            self.limited = False

        def fetch_history(self, symbol, range_):
            self.calls.append(symbol)
            if symbol == "2" and not self.limited:
                self.limited = True
                raise ProviderRateLimit("cuota agotada", retry_after=3)
            return {"currency": "USD", "bars": [(date(2024, 1, 1), Decimal(1))]}

    waits = []
    monkeypatch.setattr("modules.pricing.history.time.sleep", waits.append)
    provider = Provider()
    result = refresh_history(provider, tickers=instruments)
    assert set(result["saved"]) == set(instruments)
    assert result["failed"] == {}
    assert provider.calls == ["0", "1", "2", "2", "3"]
    assert waits == [4]


@pytest.mark.django_db
def test_error_transitorio_real_sigue_interrumpiendo_el_lote(monkeypatch):
    instruments = {
        str(i): Instrument.objects.create(name=str(i), asset_class="STOCK", currency="USD") for i in range(3)
    }

    class Provider:
        name = "SIMULADO"

        def fetch_history(self, symbol, range_):
            raise ProviderError("proveedor no disponible", transient=True)

    waits = []
    monkeypatch.setattr("modules.pricing.history.time.sleep", waits.append)
    result = refresh_history(Provider(), tickers=instruments)
    assert "_abortado" in result["failed"]
    assert waits == []
