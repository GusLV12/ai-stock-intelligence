import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

BUY = {"operation": "BUY", "quantity": "3.50", "price_per_unit": "100", "executed_at": "2026-01-05T15:00:00Z"}
SELL = {
    "operation": "SELL", "quantity": "1.25", "price_per_unit": "130", "fees": "1.00",
    "executed_at": "2026-02-05T15:00:00Z",
}  # fmt: skip


@pytest.fixture
def client(db):
    c = APIClient()
    c.force_authenticate(get_user_model().objects.create_user("ana", password="x"))
    return c


@pytest.fixture
def setup(client):
    acc = client.post("/api/v1/accounts", {"name": "GBM", "broker": "GBM_USA"}, format="json").json()
    ins = client.post(
        "/api/v1/instruments",
        {"asset_class": "STOCK", "name": "Ficticia", "market": "NASDAQ",
         "identifiers": [{"kind": "TICKER", "value": "FIC"}]},
        format="json",
    ).json()  # fmt: skip
    return acc["id"], ins["id"]


def test_requiere_autenticacion(db):
    for url in ("/api/v1/accounts", "/api/v1/portfolio/summary", "/api/v1/portfolio/transactions"):
        assert APIClient().get(url).status_code in (401, 403)


def test_flujo_compra_venta_resumen(client, setup):
    acc, ins = setup
    r = client.post("/api/v1/portfolio/transactions", {**BUY, "account": acc, "instrument": ins}, format="json")
    assert r.status_code == 201, r.content
    r = client.post("/api/v1/portfolio/transactions", {**SELL, "account": acc, "instrument": ins}, format="json")
    assert r.status_code == 201, r.content

    pos = client.get("/api/v1/portfolio/positions").json()
    assert len(pos) == 1
    assert pos[0]["quantity"].startswith("2.25") and pos[0]["realized_pnl"].startswith("36.5")

    summary = client.get("/api/v1/portfolio/summary").json()
    assert summary["by_currency"]["USD"]["realized_pnl"].startswith("36.5")

    sales = client.get("/api/v1/portfolio/sales").json()
    assert len(sales) == 1
    assert sales[0]["tax_status"] == "fiscal_pendiente"
    assert sales[0]["realized_pnl_book"].startswith("36.5")


def test_venta_mayor_al_saldo_devuelve_400(client, setup):
    acc, ins = setup
    r = client.post("/api/v1/portfolio/transactions", {**SELL, "account": acc, "instrument": ins}, format="json")
    assert r.status_code == 400
    assert "Saldo insuficiente" in str(r.json())
    assert client.get("/api/v1/portfolio/transactions").json() == []


def test_no_se_ve_ni_se_usa_cuenta_de_otro_usuario(client, setup):
    acc, ins = setup
    otro = APIClient()
    otro.force_authenticate(get_user_model().objects.create_user("beto", password="x"))
    assert otro.get("/api/v1/accounts").json() == []
    assert otro.get(f"/api/v1/accounts/{acc}").status_code == 404
    r = otro.post("/api/v1/portfolio/transactions", {**BUY, "account": acc, "instrument": ins}, format="json")
    assert r.status_code == 400


def test_cuenta_duplicada_rechazada(client, setup):
    r = client.post("/api/v1/accounts", {"name": "GBM", "broker": "GBM_USA"}, format="json")
    assert r.status_code == 400
