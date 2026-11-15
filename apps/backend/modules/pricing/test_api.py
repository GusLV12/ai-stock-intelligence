from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.instruments.models import Instrument

from .models import MarketPrice


@pytest.fixture
def ctx(db):
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_user("ana", password="x"))
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=8)
    return client, ins


def when(hours_ago=2):
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def test_registrar_y_consultar_precio_manual(ctx):
    client, ins = ctx
    r = client.post("/api/v1/market/prices", {"instrument": ins.id, "price": "123.45", "observed_at": when()})
    assert r.status_code == 201, r.content
    assert r.json()["provider"] == "MANUAL" and r.json()["age_hours"] >= 2
    latest = client.get(f"/api/v1/market/prices?instrument={ins.id}").json()
    assert latest["price"] == "123.45"
    assert len(client.get("/api/v1/market/prices").json()) == 1


def test_el_proveedor_no_se_puede_falsificar(ctx):
    client, ins = ctx
    client.post("/api/v1/market/prices",
                {"instrument": ins.id, "price": "1", "observed_at": when(), "provider": "GBM_REPORT"})  # fmt: skip
    assert MarketPrice.objects.get().provider == "MANUAL"


def test_misma_observacion_no_duplica(ctx):
    client, ins = ctx
    body = {"instrument": ins.id, "price": "10", "observed_at": when()}
    assert client.post("/api/v1/market/prices", body).status_code == 201
    assert client.post("/api/v1/market/prices", body).status_code == 200
    assert MarketPrice.objects.count() == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"price": "10", "observed_at": "2026-01-01T00:00:00+00:00"},  # sin instrumento
        {"instrument": 1, "price": "abc", "observed_at": "2026-01-01T00:00:00+00:00"},
        {"instrument": 1, "price": "NaN", "observed_at": "2026-01-01T00:00:00+00:00"},
        {"instrument": 1, "price": "-5", "observed_at": "2026-01-01T00:00:00+00:00"},
        {"instrument": 1, "price": "5", "observed_at": "2026-01-01T00:00:00"},  # sin zona
        {"instrument": 1, "price": "5", "observed_at": "2999-01-01T00:00:00+00:00"},  # futuro
        {"instrument": 1, "price": "5", "observed_at": "ayer"},
    ],
)
def test_entradas_invalidas_dan_400(ctx, payload):
    client, ins = ctx
    if payload.get("instrument") == 1:
        payload = {**payload, "instrument": ins.id}
    assert client.post("/api/v1/market/prices", payload).status_code == 400


def test_tipo_de_cambio(ctx):
    client, _ = ctx
    r = client.post(
        "/api/v1/market/fx", {"base": "usd", "quote": "mxn", "rate": "18.4", "observed_at": when()}
    )
    assert r.status_code == 201 and r.json()["pair"] == "USD/MXN"
    assert client.get("/api/v1/market/fx").json()["rate"] == "18.4"
    assert client.get("/api/v1/market/fx?base=EUR&quote=MXN").status_code == 404
    assert client.get("/api/v1/market/prices?instrument=999999").status_code == 404
    bad = client.post(
        "/api/v1/market/fx", {"base": "USD", "quote": "USD", "rate": "1", "observed_at": when()}
    )
    assert bad.status_code == 400


def test_requiere_autenticacion(db):
    for url in ("/api/v1/market/prices", "/api/v1/market/fx"):
        assert APIClient().get(url).status_code in (401, 403)
