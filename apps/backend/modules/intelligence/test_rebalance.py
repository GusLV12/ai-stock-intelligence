from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.imports.models import PositionSnapshot
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy
from modules.pricing.services import record_price

from .holdings import load_holdings
from .rebalance import RebalanceError, propose

T0 = datetime(2026, 1, 1, tzinfo=UTC)
NOW = datetime.now(UTC)


def make(ticker, scale=8):
    ins = Instrument.objects.create(asset_class="STOCK", name=ticker, quantity_scale=scale)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=ticker)
    return ins


@pytest.fixture
def world(db):
    user = get_user_model().objects.create_user("ana", password="x")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    for t, qty in (("AAA", 6), ("BNK", 2), ("GLD", 1), ("BTC", 1)):
        ins = make(t)
        record_buy(account=acc, instrument=ins, quantity=D(qty), price=D(100), executed_at=T0)
        record_price(ins, D(100), "MANUAL", NOW)
    PositionSnapshot.objects.create(account=acc, file_sha256="a" * 64, as_of=NOW, cash_amount=D(100))
    return user, acc


def trades(res):
    return {t["ticker"]: t for t in res["trades"]}


TARGETS = {"AAA": D(40), "BNK": D(20), "GLD": D(20), "BTC": D(10)}  # suma 90; 10% efectivo


def test_primero_aportaciones_sin_vender(world):
    # Total 1100 + 200 = 1300. Compras necesarias 250 (BNK 60, GLD 160, BTC 30) > disponible 170 => 68%.
    res = propose(load_holdings(world[0])["holdings"], TARGETS, contribution=D(200))
    t = trades(res)
    assert t["AAA"]["side"] is None  # no se vende por defecto
    assert t["BNK"]["side"] == "BUY" and round(t["BNK"]["amount"], 6) == D("40.8")
    assert round(t["GLD"]["amount"], 6) == D("108.8") and round(t["BTC"]["amount"], 6) == D("20.4")
    assert round(res["summary"]["buys_usd"], 6) == D(170) and round(res["summary"]["cash_after_usd"], 6) == D(
        130
    )
    assert any("68%" in w for w in res["warnings"]) and res["summary"]["max_residual_drift_pct"] > 0
    assert any("no se envía ninguna orden" in w for w in res["warnings"]) and any(
        "impuestos" in w for w in res["warnings"]
    )


def test_con_ventas_permitidas_llega_exacto_al_objetivo(world):
    res = propose(load_holdings(world[0])["holdings"], TARGETS, contribution=D(200), allow_sells=True)
    t = trades(res)
    assert t["AAA"]["side"] == "SELL" and round(t["AAA"]["amount"], 6) == D(80)  # 600 -> 520
    for tk in TARGETS:
        assert abs(t[tk]["drift_after_pct"]) < D("0.0001"), tk
    assert round(res["summary"]["cash_after_usd"], 6) == D(130) and res["summary"][
        "max_residual_drift_pct"
    ] < D("0.0001")


def test_sin_aportacion_y_sin_ventas_solo_se_usa_el_efectivo_sobrante(world):
    res = propose(
        load_holdings(world[0])["holdings"], {"AAA": D(20), "BNK": D(20), "GLD": D(30), "BTC": D(20)}
    )
    # objetivo de efectivo = 10% de 1100 = 110, hay 100: no hay fondos para comprar
    assert all(t["side"] is None for t in res["trades"]) and any(
        "no hay fondos" in w for w in res["warnings"]
    )


def test_titulos_se_redondean_hacia_abajo_y_el_remanente_queda_en_efectivo(db):
    user = get_user_model().objects.create_user("u", password="x")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    entero = make("ENT", scale=0)
    record_buy(account=acc, instrument=entero, quantity=D(1), price=D(30), executed_at=T0)
    record_price(entero, D(30), "MANUAL", NOW)
    PositionSnapshot.objects.create(account=acc, file_sha256="b" * 64, as_of=NOW, cash_amount=D(100))
    res = propose(load_holdings(user)["holdings"], {"ENT": D(100)}, contribution=D(0))
    t = trades(res)["ENT"]
    # objetivo 130 en ENT => comprar 100 => 3.33 -> 3 títulos (90), el resto queda en efectivo
    assert t["side"] == "BUY" and t["shares"] == D(3) and t["amount"] == D(90)
    assert res["summary"]["cash_after_usd"] == D(10)


def test_comision_por_operacion_reduce_los_fondos(world):
    sin = propose(load_holdings(world[0])["holdings"], TARGETS, contribution=D(200))
    con = propose(
        load_holdings(world[0])["holdings"], TARGETS, contribution=D(200), commission_per_trade=D(2)
    )
    assert (
        con["summary"]["estimated_commissions_usd"] == D(6)
        and con["summary"]["buys_usd"] < sin["summary"]["buys_usd"]
    )
    assert round(con["summary"]["cash_after_usd"], 6) == round(
        sin["summary"]["cash_after_usd"] - D(6) + (sin["summary"]["buys_usd"] - con["summary"]["buys_usd"]), 6
    )


def test_misma_emisora_en_dos_cuentas_se_suma(world):
    user, _ = world
    other = InvestmentAccount.objects.create(owner=user, name="Segunda", broker="GBM_USA")
    aaa = InstrumentIdentifier.objects.get(kind="TICKER", value="AAA").instrument
    record_buy(account=other, instrument=aaa, quantity=D(2), price=D(100), executed_at=T0)
    res = propose(load_holdings(user)["holdings"], TARGETS, contribution=D(200))
    assert res["total_before_usd"] == D(1300)
    assert trades(res)["AAA"]["current_value"] == D(800)
    assert any("cuentas" in warning for warning in res["warnings"])


def test_comision_sin_fondos_no_produce_efectivo_negativo(world):
    holdings = [h for h in load_holdings(world[0])["holdings"] if h.ticker == "AAA"]
    with pytest.raises(RebalanceError, match="comisiones"):
        propose(holdings, {"AAA": D(0)}, allow_sells=True, commission_per_trade=D(1000))


def test_posiciones_sin_objetivo_quedan_fijas_y_objetivos_desconocidos_se_ignoran(world):
    res = propose(
        load_holdings(world[0])["holdings"],
        {"AAA": D(30), "ZZZ": D(10)},
        contribution=D(100),
        allow_sells=True,
    )
    t = trades(res)
    assert t["BNK"]["side"] is None and t["BNK"]["target_weight_pct"] is None  # sin objetivo: no se toca
    assert any("ZZZ" in w for w in res["warnings"])


@pytest.mark.parametrize(
    "targets,kw,msg",
    [({}, {}, "pesos objetivo"), ({"AAA": D(60), "BNK": D(50)}, {}, "más de 100"), ({"AAA": D(10)}, {"contribution": D(-1)}, "negativas"),
     ({"AAA": D(95)}, {}, "exceden")],  # AAA 95% + BNK/GLD/BTC fijos (20%+...) > 100
)  # fmt: skip
def test_entradas_invalidas(world, targets, kw, msg):
    with pytest.raises(RebalanceError, match=msg):
        propose(load_holdings(world[0])["holdings"], targets, **kw)


def test_posicion_sin_precio_se_trata_como_fija(world):
    user, acc = world
    sin = make("NOP")
    record_buy(account=acc, instrument=sin, quantity=D(5), price=D(10), executed_at=T0)
    res = propose(load_holdings(user)["holdings"], {"AAA": D(40), "NOP": D(10)}, contribution=D(100))
    assert any("NOP" in w for w in res["warnings"]) and "NOP" not in trades(res)


# ---------- API ----------


def client_for(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_api_usa_la_politica_si_no_se_envian_objetivos(world):
    c = client_for(world[0])
    r = c.post("/api/v1/portfolio/rebalance-preview", {"contribution": 200}, format="json")
    assert r.status_code == 400 and "política" in str(r.json())
    c.post(
        "/api/v1/portfolio/policies",
        {"name": "Objetivo", "target_weights": {"AAA": 40, "BNK": 20, "GLD": 20, "BTC": 10}},
        format="json",
    )
    r = c.post("/api/v1/portfolio/rebalance-preview", {"contribution": 200}, format="json")
    assert r.status_code == 200, r.content
    d = r.json()
    assert "Objetivo" in d["targets_source"] and d["summary"]["cash_after_usd"] == "130.000000"
    assert "no es una orden" in d["disclaimer"]
    # los objetivos del request tienen prioridad sobre la política
    r2 = c.post(
        "/api/v1/portfolio/rebalance-preview",
        {"targets": {"AAA": 50}, "allow_sells": True, "contribution": "0"},
        format="json",
    )
    assert r2.json()["targets_source"] == "request"


@pytest.mark.parametrize(
    "body",
    [
        {"targets": {"AAA": 150}},
        {"targets": {"AAA": "x"}},
        {"targets": "no"},
        {"targets": {"AAA": 10}, "contribution": "abc"},
        {"targets": {"AAA": 10}, "contribution": -5},
        {"targets": {"AAA": 10}, "commission_per_trade": "NaN"},
        {"targets": {"AAA": 60, "BNK": 60}},
    ],
)
def test_api_valida_entradas(world, body):
    assert (
        client_for(world[0]).post("/api/v1/portfolio/rebalance-preview", body, format="json").status_code
        == 400
    )


def test_api_aislamiento_y_autenticacion(world):
    otro = get_user_model().objects.create_user("beto", password="x")
    r = client_for(otro).post("/api/v1/portfolio/rebalance-preview", {"targets": {"AAA": 10}}, format="json")
    assert r.status_code == 400  # no tiene valor que rebalancear
    assert APIClient().post("/api/v1/portfolio/rebalance-preview", {}, format="json").status_code in (
        401,
        403,
    )
