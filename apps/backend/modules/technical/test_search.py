from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.instruments.models import Instrument
from modules.pricing.history_models import DailyClose
from modules.technical.models import TechnicalClose
from modules.technical.test_api import client_for
from modules.technical.test_explorer import world  # noqa: F401


def test_seguimiento_aislado_y_persistente(world):  # noqa: F811
    user, instruments = world
    client = client_for(user)
    url = "/api/v1/technical/watchlist"
    assert APIClient().get(url).status_code in (401, 403)
    assert client.post(url, {"instrument_id": instruments["BBB"].id}, format="json").status_code == 201
    assert client.post(url, {"instrument_id": instruments["BBB"].id}, format="json").status_code == 201
    assert [r["ticker"] for r in client.get(url).json()] == ["BBB"]
    other = client_for(get_user_model().objects.create_user("otro-seguimiento", password="x"))
    assert other.get(url).json() == []
    assert other.delete(f"{url}/{instruments['BBB'].id}").status_code == 404
    assert client.delete(f"{url}/{instruments['BBB'].id}").status_code == 204


def test_busqueda_carga_explicita_y_procedencia(world, monkeypatch):  # noqa: F811
    from modules.technical import search_api

    client = client_for(world[0])
    row = {
        "symbol": "ZZZ",
        "name": "Ejemplo",
        "market": "NMS",
        "currency": "USD",
        "kind": "STOCK",
        "source": "YAHOO_UNOFFICIAL",
    }
    monkeypatch.setattr(search_api, "_search_provider", lambda query: ([row], []))
    response = client.get("/api/v1/technical/market-search?q=ZZZ")
    assert response.status_code == 200
    assert not Instrument.objects.filter(name="Ejemplo").exists()
    token = response.json()["results"][0]["token"]

    class Provider:
        name = "YAHOO_UNOFFICIAL"

        def _meta(self, symbol):
            return {"exchangeName": "NMS", "currency": "USD"}

        def fetch_history(self, symbol, range_):
            return {"currency": "USD", "bars": [(date(2025, 1, 1), Decimal("12.5"))]}

    monkeypatch.setattr(search_api, "YahooProvider", Provider)
    other = client_for(get_user_model().objects.create_user("otro-mercado", password="x"))
    assert other.post("/api/v1/technical/market-history", {"token": token}, format="json").status_code == 400
    loaded = client.post("/api/v1/technical/market-history", {"token": token}, format="json")
    assert loaded.status_code == 200
    assert loaded.json()["provider"] == "YAHOO_UNOFFICIAL"
    assert (
        TechnicalClose.objects.filter(
            instrument_id=loaded.json()["instrument_id"], provider="YAHOO_UNOFFICIAL"
        ).count()
        == 1
    )
    assert client.post("/api/v1/technical/market-history", {"token": token}, format="json").status_code == 200
    assert TechnicalClose.objects.filter(instrument_id=loaded.json()["instrument_id"]).count() == 1
    assert not DailyClose.objects.filter(instrument_id=loaded.json()["instrument_id"]).exists()

    monkeypatch.setattr(Provider, "_meta", lambda self, symbol: {"exchangeName": "NYQ", "currency": "USD"})
    assert client.post("/api/v1/technical/market-history", {"token": token}, format="json").status_code == 400
    assert client.get("/api/v1/technical/market-search?q=x").status_code == 400


def test_busqueda_respaldo_y_error_sin_exponer_credenciales(world, monkeypatch):  # noqa: F811
    from modules.pricing.providers import ProviderError
    from modules.technical import search_api

    monkeypatch.setenv("TWELVE_DATA_API_KEY", "valor-ficticio")
    calls = []

    def provider(url, **kwargs):
        calls.append(url.split("?")[0])
        if "twelvedata" in url:
            raise ProviderError("fallo simulado")
        return {
            "quotes": [{"symbol": "ABC", "shortname": "Ejemplo", "exchange": "NYQ", "quoteType": "EQUITY"}]
        }

    monkeypatch.setattr(search_api, "http_get_json", provider)
    client = client_for(world[0])
    response = client.get("/api/v1/technical/market-search?q=ABC")
    assert response.status_code == 200
    assert response.json()["results"][0]["source"] == "YAHOO_UNOFFICIAL"
    assert response.json()["warnings"]
    assert "valor-ficticio" not in str(response.json())
    assert len(calls) == 2
    assert (
        client.post("/api/v1/technical/watchlist", {"instrument_id": "no-id"}, format="json").status_code
        == 400
    )
