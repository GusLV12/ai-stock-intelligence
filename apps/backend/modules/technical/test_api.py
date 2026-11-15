import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy
from modules.pricing.history_models import DailyClose
from modules.pricing.services import record_price


def client_for(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def load_bars(ins, n=260, ohlc=True, end_offset=1, provider="YAHOO_UNOFFICIAL"):
    today = datetime.now(UTC).date()
    rows = []
    for i in range(n):
        day = today - timedelta(days=n - i + end_offset - 1)
        c = 100 + math.sin(i / 7) * 6 + i * 0.1
        rows.append(DailyClose(
            instrument=ins, date=day, close=D(str(round(c, 4))), currency="USD", provider=provider,
            open=D(str(round(c - 0.3, 4))) if ohlc else None, high=D(str(round(c + 1, 4))) if ohlc else None,
            low=D(str(round(c - 1, 4))) if ohlc else None, volume=1000 + i if ohlc else None,
        ))  # fmt: skip
    DailyClose.objects.bulk_create(rows)


@pytest.fixture
def world(db):
    user = get_user_model().objects.create_user("ana", password="x")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    out = {}
    for t in ("AAA", "BBB"):
        ins = Instrument.objects.create(asset_class="STOCK", name=t, quantity_scale=8)
        InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=t)
        record_buy(account=acc, instrument=ins, quantity=D(1), price=D(100), executed_at=datetime(2026, 1, 1, tzinfo=UTC))
        record_price(ins, D(100), "MANUAL", datetime.now(UTC))
        out[t] = ins
    return user, out


def test_analyze_devuelve_velas_series_senales_y_frescura(world):
    user, ins = world
    load_bars(ins["AAA"])
    d = client_for(user).get("/api/v1/technical/analyze?ticker=aaa&days=250").json()
    assert d["evaluable"] and d["has_ohlc"] and len(d["candles"]) == 250 and d["bars"] == 250
    assert d["candles"][0]["open"] is not None and d["candles"][0]["volume"] is not None
    s = d["series"]
    assert len(s["ema_slow"]) == 250 - 199 and len(s["sma"]) == 250 - 19  # calentamiento descartado
    assert s["rsi"][0]["time"] and 0 <= float(s["rsi"][-1]["value"]) <= 100
    ids = {x["id"] for x in d["signals"]}
    assert {"breakout", "reclaim_ma", "oversold", "trend_up", "macd_bull_cross", "volume_spike", "near_level"} <= ids
    assert all(x["rule"] and "fired" in x for x in d["signals"])
    f = d["freshness"]
    assert f["provider"] == "YAHOO_UNOFFICIAL" and f["age_days"] == 1 and f["stale"] is False and "no son tiempo real" in f["note"]
    assert d["params"]["rsi_days"] == 14 and "no son pruebas independientes" in d["note"]


def test_las_fechas_de_la_serie_coinciden_con_las_velas(world):
    user, ins = world
    load_bars(ins["AAA"], n=120)
    d = client_for(user).get("/api/v1/technical/analyze?ticker=AAA").json()
    candle_dates = [c["time"] for c in d["candles"]]
    assert all(p["time"] in candle_dates for p in d["series"]["sma"])
    assert d["series"]["sma"][-1]["time"] == candle_dates[-1]


def test_parametros_por_query_se_aplican_y_se_validan(world):
    user, ins = world
    load_bars(ins["AAA"])
    c = client_for(user)
    d = c.get("/api/v1/technical/analyze?ticker=AAA&breakout_days=10&rsi_days=7").json()
    assert d["params"]["breakout_days"] == 10 and d["params"]["rsi_days"] == 7
    assert "10 cierres" in next(x for x in d["signals"] if x["id"] == "breakout")["rule"]
    assert c.get("/api/v1/technical/analyze?ticker=AAA&breakout_days=1").status_code == 400
    assert c.get("/api/v1/technical/analyze?ticker=AAA&rsi_days=x").status_code == 400
    ignorado = c.get("/api/v1/technical/analyze?ticker=AAA&inventado=5")
    assert ignorado.status_code == 200  # los parámetros desconocidos del query se ignoran


def test_datos_viejos_se_marcan_como_desactualizados(world):
    user, ins = world
    load_bars(ins["AAA"], end_offset=12)
    f = client_for(user).get("/api/v1/technical/analyze?ticker=AAA").json()["freshness"]
    assert f["stale"] is True and f["age_days"] == 12


def test_sin_velas_completas_no_hay_atr_ni_plan_pero_si_el_resto(world):
    user, ins = world
    load_bars(ins["AAA"], ohlc=False)
    d = client_for(user).get("/api/v1/technical/analyze?ticker=AAA").json()
    assert d["has_ohlc"] is False and d["series"]["atr"] == [] and d["series"]["rvol"] == []
    assert d["plan_suggestion"] is None and d["series"]["rsi"]
    byid = {x["id"]: x for x in d["signals"]}
    assert byid["volume_spike"]["fired"] is None and byid["near_level"]["fired"] is None


@pytest.mark.parametrize("qs,code", [("", 400), ("ticker=ZZZ", 400), ("ticker=AAA&days=5", 400), ("ticker=AAA&days=99999", 400),
                                     ("ticker=AAA&days=x", 400)])  # fmt: skip
def test_analyze_validaciones(world, qs, code):
    assert client_for(world[0]).get(f"/api/v1/technical/analyze?{qs}").status_code == code


def test_ticker_sin_historial(world):
    d = client_for(world[0]).get("/api/v1/technical/analyze?ticker=AAA").json()
    assert d["evaluable"] is False and "refresh_price_history" in d["note"]


def test_scan_y_symbols(world):
    user, ins = world
    load_bars(ins["AAA"])
    c = client_for(user)
    scan = c.get("/api/v1/technical/scan").json()
    rows = {r["ticker"]: r for r in scan["rows"]}
    assert rows["AAA"]["evaluable"] and "fired" in rows["AAA"] and rows["BBB"]["evaluable"] is False
    assert "no son recomendaciones" in scan["note"]
    syms = {s["ticker"]: s for s in c.get("/api/v1/technical/symbols").json()}
    assert syms["AAA"]["bars"] == 260 and syms["AAA"]["has_ohlc"] is True and syms["AAA"]["held"] is True
    assert syms["BBB"]["bars"] == 0 and syms["BBB"]["last_bar"] is None


def test_trade_plan_ejemplo_del_plan_y_validaciones(world):
    c = client_for(world[0])
    r = c.post("/api/v1/technical/trade-plan", {"entry": 100, "stop": 98, "target": 104, "capital": 10000, "risk_budget": 50}, format="json")
    assert r.status_code == 200, r.content
    d = r.json()
    assert d["shares"] == "25.000000" and d["verdict"] == "PASS" and d["reward_risk_gross"] == "2.000000"
    blocked = c.post("/api/v1/technical/trade-plan", {"entry": 100, "stop": 98, "target": 102, "capital": 10000, "risk_budget": 50,
                                                      "limits": {"min_reward_risk": "1.5"}}, format="json").json()  # fmt: skip
    assert blocked["verdict"] == "BLOCK" and blocked["blocked_by"] == ["min_reward_risk"]
    for bad in ({}, {"entry": 100, "stop": 101, "target": 104, "capital": 10000}, {"entry": "x", "stop": 1, "target": 2, "capital": 3},
                {"entry": 100, "stop": 98, "target": 104, "capital": 10000, "side": "SELL"},
                {"entry": 100, "stop": 98, "target": 104, "capital": 10000, "limits": {"otra": 1}},
                {"entry": 100, "stop": 98, "target": 104, "capital": 10000, "shares_scale": "x"}):
        assert c.post("/api/v1/technical/trade-plan", bad, format="json").status_code == 400


def test_autenticacion(world):
    for method, url in (("get", "/api/v1/technical/analyze?ticker=AAA"), ("get", "/api/v1/technical/scan"),
                        ("get", "/api/v1/technical/symbols"), ("post", "/api/v1/technical/trade-plan")):
        assert getattr(APIClient(), method)(url).status_code in (401, 403)
