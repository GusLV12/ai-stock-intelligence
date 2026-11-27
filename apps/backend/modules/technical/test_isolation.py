from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.core.models import ProviderQuota
from modules.core.provider_quota import QuotaUnavailable, reserve
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.pricing.history_models import DailyClose
from modules.quant.models import QuantUniverseMember
from modules.quant.services import load_series
from modules.technical.api import _bars_for, _bulk_bars_for, _find_instrument
from modules.technical.models import TechnicalClose


@pytest.mark.django_db
def test_historial_tecnico_aislado_hasta_incorporacion_explicita():
    user = get_user_model().objects.create_user("investigador")
    client = APIClient()
    client.force_authenticate(user)
    ins = Instrument.objects.create(name="Ejemplo", market="NMS", currency="USD", asset_class="STOCK")
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="TEST")
    TechnicalClose.objects.bulk_create(
        [
            TechnicalClose(
                instrument=ins,
                date=date(2024, 1, 1) + timedelta(days=i),
                close=Decimal(12),
                currency="USD",
                provider="YAHOO_UNOFFICIAL",
            )
            for i in range(80)
        ]
    )
    assert not DailyClose.objects.filter(instrument=ins).exists()
    assert ins.id not in load_series()[0]
    assert not QuantUniverseMember.objects.filter(instrument=ins).exists()
    assert _bars_for(ins.id, 20) == _bulk_bars_for([ins.id], 20)[ins.id]
    before = client.get("/api/v1/quant/overview").json()["universe"]
    assert not any(x["instrument"] == ins.id for x in before)
    result = client.post("/api/v1/quant/universe", {"instrument_id": ins.id}, format="json")
    assert result.status_code == 200
    assert DailyClose.objects.filter(instrument=ins).count() == 80
    assert QuantUniverseMember.objects.filter(instrument=ins).exists()
    assert any(x["instrument"] == ins.id for x in client.get("/api/v1/quant/overview").json()["universe"])
    assert (
        client.post("/api/v1/quant/universe", {"instrument_id": ins.id}, format="json").json()["added"]
        is False
    )
    assert DailyClose.objects.filter(instrument=ins).count() == 80


@pytest.mark.django_db
def test_misma_emisora_ambigua_exige_eleccion(monkeypatch):
    from rest_framework.exceptions import ValidationError

    from modules.technical import search_api

    user = get_user_model().objects.create_user("investigador")
    client = APIClient()
    client.force_authenticate(user)
    ins = Instrument.objects.create(name="Ejemplo", market="US", currency="USD", asset_class="STOCK")
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="XYZ")
    row = {
        "symbol": "XYZ",
        "name": "Ejemplo",
        "market": "XNAS",
        "currency": "USD",
        "kind": "STOCK",
        "source": "TWELVE_DATA",
    }
    monkeypatch.setattr(search_api, "_search_provider", lambda _: ([row], []))

    class Provider:
        name = "YAHOO_UNOFFICIAL"

        def _meta(self, symbol):
            return {"exchangeName": "NMS", "currency": "USD"}

        def fetch_history(self, symbol, range_):
            return {"currency": "USD", "bars": [(date(2024, 1, 1), Decimal(10))]}

    monkeypatch.setattr(search_api, "YahooProvider", Provider)
    token = client.get("/api/v1/technical/market-search?q=XYZ").json()["results"][0]["token"]
    response = client.post("/api/v1/technical/market-history", {"token": token}, format="json")
    assert response.json()["requires_selection"] is True
    assert Instrument.objects.count() == 1 and not TechnicalClose.objects.exists()
    response = client.post(
        "/api/v1/technical/market-history", {"token": token, "instrument_id": ins.id}, format="json"
    )
    assert response.json()["instrument_id"] == ins.id and Instrument.objects.count() == 1
    assert not DailyClose.objects.filter(instrument=ins).exists()
    other = Instrument.objects.create(name="Otra", market="NYQ", currency="USD", asset_class="STOCK")
    InstrumentIdentifier.objects.create(instrument=other, kind="TICKER", value="XYZ")
    with pytest.raises(ValidationError):
        _find_instrument("XYZ")
    assert _find_instrument("XYZ", other.id).id == other.id


@pytest.mark.django_db
def test_cuota_compartida_no_guarda_credenciales(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_PER_MINUTE", "2")
    reserve("TWELVE_DATA")
    reserve("TWELVE_DATA")
    with pytest.raises(QuotaUnavailable):
        reserve("TWELVE_DATA")
    assert ProviderQuota.objects.get(provider="TWELVE_DATA").used == 2


@pytest.mark.django_db
def test_busquedas_repetidas_usan_cache_y_firman_por_usuario(monkeypatch):
    from django.core import signing

    from modules.technical import search_api

    calls = []
    row = {
        "symbol": "XYZ",
        "name": "Ejemplo",
        "market": "NMS",
        "currency": "USD",
        "kind": "STOCK",
        "source": "YAHOO_UNOFFICIAL",
    }
    monkeypatch.setattr(search_api, "_search_provider", lambda q: (calls.append(q) or [row], []))
    users = [
        get_user_model().objects.create_user("usuario-uno"),
        get_user_model().objects.create_user("usuario-dos"),
    ]
    tokens = []
    for user in users:
        client = APIClient()
        client.force_authenticate(user)
        response = client.get("/api/v1/technical/market-search?q=XYZ")
        tokens.append(response.json()["results"][0]["token"])
    assert len(calls) == 1
    assert [signing.loads(token, salt="technical-market")["user"] for token in tokens] == [
        u.id for u in users
    ]


@pytest.mark.django_db
def test_actualizar_tecnico_no_cambia_las_series_de_un_miembro_existente():
    ins = Instrument.objects.create(name="Ejemplo", market="US", currency="USD", asset_class="STOCK")
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="TEST")
    QuantUniverseMember.objects.create(instrument=ins)
    DailyClose.objects.bulk_create(
        [
            DailyClose(
                instrument=ins,
                date=date(2024, 1, 1) + timedelta(days=i),
                close=Decimal(10),
                currency="USD",
                provider="YAHOO_UNOFFICIAL",
            )
            for i in range(80)
        ]
    )
    before, _ = load_series()
    TechnicalClose.objects.bulk_create(
        [
            TechnicalClose(
                instrument=ins,
                date=date(2024, 1, 1) + timedelta(days=i),
                close=Decimal(12),
                currency="USD",
                provider="YAHOO_UNOFFICIAL",
            )
            for i in range(80)
        ]
    )
    after, _ = load_series()
    assert before == after
    assert _bars_for(ins.id, 1)[0][0]["close"] == 12
    assert DailyClose.objects.filter(instrument=ins, close=Decimal(10)).count() == 80
