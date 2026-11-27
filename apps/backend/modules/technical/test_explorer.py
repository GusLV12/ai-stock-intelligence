from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy
from modules.pricing.history_models import DailyClose
from modules.technical.test_api import client_for, load_bars


@pytest.fixture
def world(db):
    user = get_user_model().objects.create_user("explorador", password="x")
    account = InvestmentAccount.objects.create(owner=user, name="Cuenta", broker="GBM_USA")
    instruments = {}
    for ticker, research, currency, bars in (
        ("AAA", False, "USD", 260),
        ("BBB", True, "USD", 260),
        ("CCC", False, "USD", 10),
        ("DDD", True, "MXN", 260),
    ):
        ins = Instrument.objects.create(
            name=ticker, asset_class="STOCK", currency=currency, research_only=research
        )
        InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=ticker)
        if not research:
            record_buy(
                account=account,
                instrument=ins,
                quantity=D(1),
                price=D(100),
                executed_at=datetime(2025, 1, 1, tzinfo=UTC),
            )
        load_bars(ins, n=bars)
        instruments[ticker] = ins
    return user, instruments


def test_filtros_universos_y_datos_insuficientes(world):
    client = client_for(world[0])
    data = client.get(
        "/api/v1/technical/explore?universe=portfolio&currency=USD&rsi_min=0&ema=above&min_volume=1"
    ).json()
    assert {r["ticker"] for r in data["rows"]} <= {"AAA"}
    assert any(r["ticker"] == "CCC" and "EMA" in r["reason"] for r in data["unavailable"])
    research = client.get("/api/v1/technical/explore?universe=research&currency=USD").json()
    assert [r["ticker"] for r in research["rows"]] == ["BBB"]
    assert client.get("/api/v1/technical/explore?min_price=200&max_price=100").status_code == 400
    assert (
        client.get("/api/v1/technical/explore?currency=MXN&universe=research").json()["rows"][0]["ticker"]
        == "DDD"
    )
    assert client.get("/api/v1/technical/explore?max_age=0").json()["rows"] == []


def test_dibujos_persisten_y_pertenecen_al_usuario(world):
    user, instruments = world
    client = client_for(user)
    url = "/api/v1/technical/drawings"
    data = {
        "instrument_id": instruments["AAA"].id,
        "kind": "trend",
        "points": [{"time": "2025-02-01", "price": "110"}, {"time": "2025-01-01", "price": "100"}],
    }
    response = client.post(url, data, format="json")
    assert response.status_code == 201, response.data
    drawing = response.json()
    assert drawing["points"][0]["time"] == "2025-01-01"
    assert len(client.get(f"{url}?instrument_id={instruments['AAA'].id}").json()) == 1
    other = client_for(get_user_model().objects.create_user("otro", password="x"))
    assert other.get(f"{url}?instrument_id={instruments['AAA'].id}").json() == []
    assert other.delete(f"{url}/{drawing['id']}").status_code == 404
    assert client.delete(f"{url}/{drawing['id']}").status_code == 204
    assert client.post(url, {**data, "points": data["points"][:1]}, format="json").status_code == 400
    assert APIClient().get(f"{url}?instrument_id={instruments['AAA'].id}").status_code in (401, 403)


def test_volumen_incompleto_y_filtros_invalidos(world):
    user, instruments = world
    DailyClose.objects.filter(instrument=instruments["AAA"]).update(volume=None)
    client = client_for(user)
    data = client.get("/api/v1/technical/explore?min_volume=1").json()
    assert not any(r["ticker"] == "AAA" for r in data["rows"])
    assert any(r["ticker"] == "AAA" and "volumen" in r["reason"] for r in data["unavailable"])
    for query in ("rsi_min=101", "ema=otro", "min_volume=NaN", "currency=US"):
        assert client.get(f"/api/v1/technical/explore?{query}").status_code == 400
