"""API de liquidez: agregación por emisora entre cuentas, aislamiento por usuario y validación de entradas."""

from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy
from modules.pricing.services import record_price

from .test_api import client_for, load_bars

T0 = datetime(2026, 1, 5, tzinfo=UTC)
URL = "/api/v1/technical/liquidity"


def instrumento(ticker):
    ins = Instrument.objects.create(asset_class="STOCK", name=ticker, quantity_scale=8)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=ticker)
    return ins


@pytest.fixture
def mundo(db):
    ana = get_user_model().objects.create_user("ana", password="x")
    otro = get_user_model().objects.create_user("otro", password="x")
    a1 = InvestmentAccount.objects.create(owner=ana, name="A1", broker="GBM_USA")
    a2 = InvestmentAccount.objects.create(owner=ana, name="A2", broker="GBM_USA")
    ajena = InvestmentAccount.objects.create(owner=otro, name="X", broker="GBM_USA")
    aaa, sin = instrumento("AAA"), instrumento("SIN")
    load_bars(aaa, n=120)  # volumen 1000+i: ~1100 títulos diarios
    record_buy(account=a1, instrument=aaa, quantity=D(30), price=D(100), executed_at=T0)
    record_buy(account=a2, instrument=aaa, quantity=D(70), price=D(100), executed_at=T0)
    record_buy(
        account=ajena, instrument=aaa, quantity=D(9999), price=D(100), executed_at=T0
    )  # no debe sumarse a ana
    record_buy(account=a1, instrument=sin, quantity=D(5), price=D(10), executed_at=T0)
    record_price(aaa, D(110), "MANUAL", datetime.now(UTC))
    record_price(sin, D(10), "MANUAL", datetime.now(UTC))
    return ana, otro


def test_liquidez_de_una_emisora_con_cantidad_hipotetica(mundo):
    ana, _ = mundo
    r = client_for(ana).get(URL, {"ticker": "aaa", "quantity": "110", "window": 20}).json()
    assert (
        r["evaluable"] is True and r["ticker"] == "AAA" and r["freshness"]["provider"] == "YAHOO_UNOFFICIAL"
    )
    assert 1000 < r["adv_shares"] < 1250 and r["tier"] == "muy baja"
    assert 0.08 < r["position"]["share_of_adv"] < 0.12 and r["position"]["size_flag"] in ("visible", "alto")
    assert "estimación" in " ".join(r["caveats"])


def test_sin_cantidad_no_hay_posicion_y_se_validan_entradas(mundo):
    ana, _ = mundo
    c = client_for(ana)
    assert "position" not in c.get(URL, {"ticker": "AAA"}).json()
    for q in ({}, {"ticker": "ZZZ"}, {"ticker": "AAA", "window": 3}, {"ticker": "AAA", "window": "x"},
              {"ticker": "AAA", "participation": 0}, {"ticker": "AAA", "quantity": "-4"}):  # fmt: skip
        assert c.get(URL, q).status_code == 400, q


def test_sin_historial_no_inventa(mundo):
    ana, _ = mundo
    r = client_for(ana).get(URL, {"ticker": "SIN"}).json()
    assert r["evaluable"] is False and "refresh_price_history" in r["reason"]


def test_cartera_suma_cuentas_propias_y_ordena_por_dias_de_operacion(mundo):
    ana, _ = mundo
    r = client_for(ana).get(URL + "/portfolio").json()
    filas = {x["ticker"]: x for x in r["rows"]}
    assert filas["AAA"]["position"]["quantity"] == 100.0  # 30 + 70; las 9999 de otro usuario no cuentan
    assert filas["SIN"]["evaluable"] is False and r["summary"]["not_evaluable"] == ["SIN"]
    assert (
        r["rows"][0]["ticker"] == "AAA" and r["summary"]["positions"] == 2 and r["summary"]["evaluable"] == 1
    )
    assert "no es una garantía" in r["disclaimer"]


def test_cartera_de_otro_usuario_esta_aislada(mundo):
    _, otro = mundo
    r = client_for(otro).get(URL + "/portfolio").json()
    assert [x["ticker"] for x in r["rows"]] == ["AAA"] and r["rows"][0]["position"]["quantity"] == 9999.0


def test_requiere_sesion(mundo):
    for u in (URL, URL + "/portfolio"):
        assert APIClient().get(u, {"ticker": "AAA"}).status_code in (401, 403)
