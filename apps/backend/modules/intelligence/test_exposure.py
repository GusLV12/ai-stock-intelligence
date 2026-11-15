from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.imports.models import PositionSnapshot
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy
from modules.pricing.services import record_fx, record_price

from .holdings import load_holdings
from .models import PortfolioPolicy

T0 = datetime(2026, 1, 1, tzinfo=UTC)
NOW = datetime.now(UTC)


def make(ticker, cls="STOCK", sector="non_financial", currency="USD"):
    ins = Instrument.objects.create(asset_class=cls, name=ticker, quantity_scale=8, sector_kind=sector, currency=currency)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=ticker)
    return ins


@pytest.fixture
def world(db):
    user = get_user_model().objects.create_user("ana", password="x")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    rows = [("AAA", "STOCK", "non_financial", 6, 100), ("BNK", "STOCK", "bank", 2, 100), ("GLD", "ETF", "non_financial", 1, 100),
            ("BTC", "CRYPTO", "non_financial", 1, 100)]  # fmt: skip
    ins = {}
    for t, cls, sec, qty, price in rows:
        i = make(t, cls, sec)
        record_buy(account=acc, instrument=i, quantity=D(qty), price=D(price), executed_at=T0)
        record_price(i, D(price), "MANUAL", NOW)
        ins[t] = i
    PositionSnapshot.objects.create(account=acc, file_sha256="a" * 64, as_of=NOW, cash_amount=D(100))
    return user, acc, ins


def client_for(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_pesos_concentracion_y_conciliacion_contra_el_dashboard(world):
    user, _, _ = world
    d = client_for(user).get("/api/v1/portfolio/exposure").json()
    assert d["evaluable"] and d["total_value_usd"] == "1100.000000"  # 600+200+100+100 + 100 de efectivo
    w = {p["ticker"]: p["weight"] for p in d["positions"]}
    assert w["AAA"] == "0.545455" and w["CASH"] == "0.090909"
    assert round(sum(float(v) for v in w.values()), 9) == 1.0
    c = d["concentration"]
    assert c["largest"] == {"ticker": "AAA", "weight": "0.545455"} and c["top3_weight"] == "0.818182"
    assert c["hhi"] == "0.355372" and c["effective_positions"] == "2.813953"  # (600²+200²+3·100²)/1100²
    assert d["reconciliation"]["difference"] == "0.000000" and d["reconciliation"]["comparable"] is True


def test_agrupaciones_por_clase_sector_moneda_y_broker(world):
    user, _, _ = world
    d = client_for(user).get("/api/v1/portfolio/exposure").json()
    assert d["by_asset_class"]["STOCK"]["weight"] == "0.727273" and d["by_asset_class"]["CRYPTO"]["weight"] == "0.090909"
    assert d["by_asset_class"]["CASH"]["weight"] == "0.090909"
    assert d["by_sector_kind"]["bank"]["weight"] == "0.200000"  # 200 de 1000 sin efectivo
    assert d["by_currency"]["USD"]["weight"] == "1.000000" and d["by_broker"]["GBM_USA"]["weight"] == "1.000000"
    assert d["etf_look_through"]["available"] is False and d["etf_look_through"]["etf_positions"] == ["GLD"]
    assert "licencia" in d["etf_look_through"]["note"]


def test_politica_reporta_incumplimientos(world):
    user, _, _ = world
    c = client_for(user)
    r = c.post("/api/v1/portfolio/policies", {"name": "Prudente", "objective": "growth", "max_position_pct": 25,
                                              "max_class_pct": {"crypto": 5}, "min_cash_pct": 15}, format="json")  # fmt: skip
    assert r.status_code == 201, r.content
    d = c.get("/api/v1/portfolio/exposure").json()
    by = {(b["type"], b["subject"]): b for b in d["breaches"]}
    assert by[("position", "AAA")]["excess_pct"] == "29.545455"
    assert ("position", "BNK") not in by  # 18.2% <= 25%
    assert by[("asset_class", "CRYPTO")]["excess_pct"] == "4.090909"
    assert by[("min_cash", "CASH")]["excess_pct"] == "5.909091"
    assert d["policy"]["name"] == "Prudente"


def test_sin_politica_no_hay_incumplimientos(world):
    user, _, _ = world
    assert client_for(user).get("/api/v1/portfolio/exposure").json()["breaches"] == []


def test_la_politica_mas_reciente_es_la_activa_y_se_conservan_las_anteriores(world):
    user, _, _ = world
    c = client_for(user)
    c.post("/api/v1/portfolio/policies", {"name": "v1", "max_position_pct": 10}, format="json")
    c.post("/api/v1/portfolio/policies", {"name": "v2", "max_position_pct": 90}, format="json")
    assert c.get("/api/v1/portfolio/exposure").json()["breaches"] == []  # con 90% AAA ya no excede
    assert [p["name"] for p in c.get("/api/v1/portfolio/policies").json()] == ["v2", "v1"]
    with pytest.raises(ProtectedError):
        PortfolioPolicy.objects.first().delete()


@pytest.mark.parametrize(
    "body",
    [{"objective": "xx"}, {"max_position_pct": 150}, {"max_position_pct": "abc"}, {"max_position_pct": "NaN"},
     {"max_class_pct": {"BONO": 10}}, {"max_class_pct": "no"}, {"target_weights": {"A": 60, "B": 50}},
     {"target_weights": {"A": -1}}, {"horizon_years": 0}, {"horizon_years": "x"}],
)  # fmt: skip
def test_politica_invalida_da_400(world, body):
    assert client_for(world[0]).post("/api/v1/portfolio/policies", body, format="json").status_code == 400


def test_posicion_sin_precio_no_se_valora_ni_entra_en_los_pesos(world):
    user, acc, _ = world
    sin = make("NOP")
    record_buy(account=acc, instrument=sin, quantity=D(5), price=D(10), executed_at=T0)
    d = client_for(user).get("/api/v1/portfolio/exposure").json()
    assert d["unpriced_positions"] == ["NOP"] and "NOP" in d["excluded"]
    assert d["total_value_usd"] == "1100.000000" and all(p["ticker"] != "NOP" for p in d["positions"])
    assert "fuera de los pesos" in d["warnings"][0]


def test_posicion_en_mxn_se_convierte_solo_con_tipo_de_cambio(world):
    user, acc, _ = world
    mx = make("MXN1", currency="MXN")
    record_buy(account=acc, instrument=mx, quantity=D(10), price=D(100), executed_at=T0)
    record_price(mx, D(100), "MANUAL", NOW)  # 1000 MXN
    sin_fx = client_for(user).get("/api/v1/portfolio/exposure").json()
    assert "MXN1" in sin_fx["excluded"] and sin_fx["reconciliation"]["comparable"] is False
    record_fx("USD", "MXN", D(20), "MANUAL", NOW)
    con_fx = client_for(user).get("/api/v1/portfolio/exposure").json()
    assert "MXN1" not in con_fx["excluded"] and con_fx["total_value_usd"] == "1150.000000"  # +1000/20 = 50 USD
    assert con_fx["fx"]["pair"] == "USD/MXN"


def test_sin_posiciones_no_es_evaluable(db):
    user = get_user_model().objects.create_user("vacio", password="x")
    d = client_for(user).get("/api/v1/portfolio/exposure").json()
    assert d["evaluable"] is False and "Sin posiciones" in d["note"]


def test_aislamiento_y_autenticacion(world):
    otro = get_user_model().objects.create_user("beto", password="x")
    assert client_for(otro).get("/api/v1/portfolio/exposure").json()["evaluable"] is False
    assert client_for(otro).get("/api/v1/portfolio/policies").json() == []
    assert APIClient().get("/api/v1/portfolio/exposure").status_code in (401, 403)
    assert [h.ticker for h in load_holdings(world[0])["holdings"] if h.asset_class == "CASH"] == ["CASH"]
