from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.imports.models import PositionSnapshot
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy
from modules.pricing.services import record_fx, record_price

from .holdings import load_holdings
from .shocks import PRESETS, ShockError, simulate

T0 = datetime(2026, 1, 1, tzinfo=UTC)
NOW = datetime.now(UTC)


def make(ticker, cls="STOCK"):
    ins = Instrument.objects.create(asset_class=cls, name=ticker, quantity_scale=8)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=ticker)
    return ins


@pytest.fixture
def world(db):
    user = get_user_model().objects.create_user("ana", password="x")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    for t, cls, qty in (("AAA", "STOCK", 6), ("BNK", "STOCK", 2), ("GLD", "ETF", 1), ("BTC", "CRYPTO", 1)):
        ins = make(t, cls)
        record_buy(account=acc, instrument=ins, quantity=D(qty), price=D(100), executed_at=T0)
        record_price(ins, D(100), "MANUAL", NOW)
    PositionSnapshot.objects.create(account=acc, file_sha256="a" * 64, as_of=NOW, cash_amount=D(100))
    record_fx("USD", "MXN", D(20), "MANUAL", NOW)
    return user


def H(user):
    return load_holdings(user)["holdings"]


def pos(res):
    return {p["ticker"]: p for p in res["positions"]}


def test_shock_a_las_acciones_ejemplo_manual(world):
    res = simulate(H(world), [{"target": "class:STOCK", "pct": D(-10)}])
    p = pos(res)
    assert (
        p["AAA"]["after_usd"] == D(540)
        and p["BNK"]["after_usd"] == D(180)
        and p["GLD"]["after_usd"] == D(100)
    )
    assert res["before_usd"] == D(1100) and res["after_usd"] == D(1020) and res["change_usd"] == D(-80)
    assert round(res["change_pct"], 4) == D("-7.2727")
    assert p["CASH"]["after_usd"] == D(100)  # el efectivo no cambia


def test_all_no_toca_el_efectivo_y_100_por_ciento_lo_lleva_a_cero(world):
    res = simulate(H(world), [{"target": "all", "pct": D(-100)}])
    assert res["after_usd"] == D(100)  # solo queda el efectivo
    assert pos(res)["AAA"]["after_usd"] == D(0)


def test_shocks_sobre_la_misma_posicion_se_componen(world):
    res = simulate(H(world), [{"target": "BTC", "pct": D(-20)}, {"target": "class:CRYPTO", "pct": D(-10)}])
    btc = pos(res)["BTC"]
    assert btc["after_usd"] == D(72) and btc["change_pct"] == D(-28) and len(btc["applied"]) == 2  # 0.8 × 0.9


def test_shock_sin_coincidencias_se_avisa(world):
    res = simulate(H(world), [{"target": "ETH", "pct": D(-25)}, {"target": "BTC", "pct": D(-20)}])
    assert any("ETH" in a for a in res["assumptions"]) and pos(res)["BTC"]["after_usd"] == D(80)


def test_componente_cambiario_separado_del_del_activo(world):
    fx_only = simulate(H(world), [], D(5), D(20))
    mv = fx_only["mxn_view"]
    assert mv["before_mxn"] == D(22000) and mv["after_mxn"] == D(23100) and mv["fx_component_mxn"] == D(1100)
    assert mv["asset_component_mxn"] == D(0) and fx_only["change_usd"] == D(0)
    both = simulate(H(world), [{"target": "class:STOCK", "pct": D(-10)}], D(5), D(20))["mxn_view"]
    assert both["after_mxn"] == D(1020) * D(21) and both["change_mxn"] == D(-580)
    assert both["asset_component_mxn"] == D(-1600) and both["fx_component_mxn"] == D(1020)
    assert (
        both["asset_component_mxn"] + both["fx_component_mxn"] == both["change_mxn"]
    )  # descomposición exacta
    assert "no es variación del precio" in both["note"]


def test_exposicion_posterior_al_shock(world):
    res = simulate(H(world), [{"target": "class:STOCK", "pct": D(-10)}])
    w = {p["ticker"]: p["weight"] for p in res["post_shock_exposure"]["positions"]}
    assert round(w["AAA"], 6) == round(D(540) / D(1020), 6)


def test_supuestos_explicitos_y_determinismo(world):
    a = simulate(H(world), [{"target": "all", "pct": D(-5)}])
    assert simulate(H(world), [{"target": "all", "pct": D(-5)}]) == a
    text = " ".join(a["assumptions"])
    assert "DETERMINISTA" in text and "beta = 1" in text and "no es una predicción" in text


@pytest.mark.parametrize(
    "args,msg",
    [
        (([], None), "al menos un shock"),
        (([{"target": "AAA", "pct": D(-150)}], None), "entre -100 y 1000"),
        (([{"target": "AAA", "pct": "x"}], None), "inválido"),
        (([{"target": "", "pct": 5}], None), "requiere target"),
        (([{"target": "AAA", "pct": D("NaN")}], None), "entre -100 y 1000"),
        (("no", None), "lista"),
        (([{"target": "AAA", "pct": D(-5)}] * 51, None), "hasta 50"),
    ],
)
def test_entradas_invalidas(world, args, msg):
    shocks, fx = args
    with pytest.raises(ShockError, match=msg):
        simulate(H(world), shocks, fx, D(20))


def test_fx_sin_tipo_de_cambio_o_fuera_de_rango(world):
    with pytest.raises(ShockError, match="tipo de cambio"):
        simulate(H(world), [], D(5), None)
    with pytest.raises(ShockError, match="entre -100%"):
        simulate(H(world), [], D(-100), D(20))


# ---------- API ----------


def client_for(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_api_presets_y_simulacion(world):
    c = client_for(world)
    presets = c.get("/api/v1/portfolio/scenarios/presets").json()
    assert {p["id"] for p in presets} == set(PRESETS)
    r = c.post("/api/v1/portfolio/scenarios", {"preset": "stocks_minus_10"}, format="json")
    assert r.status_code == 200, r.content
    assert (
        r.json()["after_usd"] == "1020.000000"
        and r.json()["mxn_view"]["asset_component_mxn"] == "-1600.000000"
    )
    fx = c.post("/api/v1/portfolio/scenarios", {"preset": "usd_mxn_plus_5"}, format="json").json()
    assert fx["mxn_view"]["fx_component_mxn"] == "1100.000000"
    mixto = c.post(
        "/api/v1/portfolio/scenarios",
        {"preset": "crypto_minus_20", "shocks": [{"target": "AAA", "pct": -50}], "fx_usd_mxn_pct": 10},
        format="json",
    ).json()
    assert mixto["mxn_view"]["rate_after"] == "22.000000"
    assert pos(mixto)["AAA"]["after_usd"] == "300.000000" and pos(mixto)["BTC"]["after_usd"] == "80.000000"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"preset": "nada"},
        {"shocks": [{"target": "AAA", "pct": -500}]},
        {"shocks": "no"},
        {"fx_usd_mxn_pct": "abc"},
        {"fx_usd_mxn_pct": "NaN"},
        {"shocks": [{"target": "AAA"}]},
    ],
)
def test_api_valida(world, body):
    assert client_for(world).post("/api/v1/portfolio/scenarios", body, format="json").status_code == 400


def test_api_sin_posiciones_y_autenticacion(world):
    otro = get_user_model().objects.create_user("beto", password="x")
    assert (
        client_for(otro)
        .post("/api/v1/portfolio/scenarios", {"preset": "stocks_minus_10"}, format="json")
        .status_code
        == 400
    )
    assert APIClient().post("/api/v1/portfolio/scenarios", {}, format="json").status_code in (401, 403)
    assert APIClient().get("/api/v1/portfolio/scenarios/presets").status_code in (401, 403)
