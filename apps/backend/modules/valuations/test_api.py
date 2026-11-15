from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from rest_framework.test import APIClient

from modules.instruments.models import Instrument
from modules.pricing.services import record_price

from .models import Valuation

DCF = {
    "fcff_base": 100, "growth": 0, "years": 5, "wacc": "0.10", "terminal_growth": 0,
    "net_debt": 0, "diluted_shares": 10,
}  # fmt: skip


@pytest.fixture
def client(db):
    c = APIClient()
    c.force_authenticate(get_user_model().objects.create_user("ana", password="x"))
    return c


def post(client, url, body):
    return client.post(f"/api/v1/valuations/{url}", body, format="json")


def test_fair_value_se_guarda_como_instantanea_y_se_recupera(client):
    r = post(client, "fair-value", {"instrument_class": "STOCK", "market_price": 80, "label": "AAA demo", "dcf": DCF,
                                    "financials_as_of": "2999-01-01"})  # fmt: skip
    assert r.status_code == 201, r.content
    d = r.json()
    assert d["methods"][0]["value_per_share"] == "100.000000" and d["margin_of_safety_base"] == "0.200000"
    assert d["assumptions_version"] == "valuation-v1"
    got = client.get(f"/api/v1/valuations/{d['id']}").json()
    assert got["label"] == "AAA demo" and got["inputs"]["dcf"]["wacc"] == "0.10"  # supuestos reproducibles
    assert got["result"]["central_value"] == "100.000000"
    listing = client.get("/api/v1/valuations/fair-value").json()
    assert listing[0]["id"] == d["id"] and listing[0]["central_value"] == "100.000000"


def test_usa_el_precio_observado_del_instrumento_con_su_procedencia(client):
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=8)
    record_price(ins, D(80), "MANUAL", datetime(2026, 11, 13, tzinfo=UTC))
    r = post(client, "fair-value", {"instrument": ins.id, "dcf": DCF})
    assert r.status_code == 201, r.content
    d = r.json()
    assert d["market_price"] == "80.000000" and d["price_source"]["provider"] == "MANUAL"
    assert Valuation.objects.get().instrument_id == ins.id


def test_instrumento_sin_precio_ni_market_price_da_400(client):
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA")
    r = post(client, "fair-value", {"instrument": ins.id, "dcf": DCF})
    assert r.status_code == 400 and "precio" in str(r.json())
    assert post(client, "fair-value", {"instrument": 99999, "dcf": DCF}).status_code == 400


@pytest.mark.parametrize(
    "body",
    [{}, {"instrument_class": "STOCK", "market_price": 80},
     {"instrument_class": "STOCK", "market_price": 80, "dcf": {"fcff_base": "x"}},
     {"instrument_class": "STOCK", "market_price": 80, "dcf": "no-es-objeto"},
     {"instrument_class": "STOCK", "market_price": 80, "dcf": {**DCF, "wacc": "0.01", "terminal_growth": "0.02"}},
     {"instrument_class": "STOCK", "market_price": 80, "dcf": {**DCF, "years": "abc"}}],
)  # fmt: skip
def test_entradas_invalidas_dan_400_sin_guardar(client, body):
    r = post(client, "fair-value", body)
    assert r.status_code == 400 and "detail" in r.json()
    assert Valuation.objects.count() == 0


def test_etf_no_evaluable_se_guarda_sin_cifra(client):
    r = post(client, "fair-value", {"instrument_class": "ETF", "market_price": 50, "dcf": DCF})
    assert r.status_code == 201 and r.json()["evaluable"] is False and r.json()["central_value"] is None


def test_las_valoraciones_son_inmutables_y_privadas(client):
    vid = post(client, "fair-value", {"instrument_class": "STOCK", "market_price": 80, "dcf": DCF}).json()[
        "id"
    ]
    with pytest.raises(ProtectedError):
        Valuation.objects.get(pk=vid).delete()
    otro = APIClient()
    otro.force_authenticate(get_user_model().objects.create_user("beto", password="x"))
    assert otro.get(f"/api/v1/valuations/{vid}").status_code == 404
    assert otro.get("/api/v1/valuations/fair-value").json() == []


def test_calculadora_dcf_con_sensibilidad_no_guarda(client):
    r = post(client, "dcf", DCF)
    assert r.status_code == 200
    d = r.json()
    assert d["applicable"] and d["value_per_share"] == "100.000000"
    assert len(d["sensitivity"]["value_per_share"]) == 5 and Valuation.objects.count() == 0
    assert post(client, "dcf", {**DCF, "wacc": "0.01", "terminal_growth": "0.02"}).status_code == 400


def test_dcf_inverso_encuentra_el_crecimiento_implicito(client):
    # Precio = valor con crecimiento 7% => debe recuperar ~7%.
    price = post(client, "dcf", {**DCF, "growth": "0.07"}).json()["value_per_share"]
    body = {k: v for k, v in DCF.items() if k != "growth"} | {"price": price}
    r = post(client, "reverse-dcf", body)
    assert r.status_code == 200 and r.json()["solved"] is True
    assert abs(float(r.json()["implied_growth"]) - 0.07) < 0.0005
    assert post(client, "reverse-dcf", {**body, "price": 0}).status_code == 400


def test_requiere_autenticacion(db):
    for method, url in (("get", "fair-value"), ("post", "dcf"), ("post", "reverse-dcf")):
        assert getattr(APIClient(), method)(f"/api/v1/valuations/{url}").status_code in (401, 403)
