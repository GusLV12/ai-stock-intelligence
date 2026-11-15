import math
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from . import indicators as ind
from .signals import SignalError, analyze, resolve_params
from .trade_plan import PlanError, evaluate

START = date(2025, 1, 1)


def make_bars(closes, volumes=None, spread=1.0):
    bars = []
    for i, c in enumerate(closes):
        bars.append({"date": START + timedelta(days=i), "open": c, "high": c + spread, "low": c - spread, "close": c,
                     "volume": None if volumes is None else volumes[i]})  # fmt: skip
    return bars


def sig(res):
    return {s["id"]: s for s in res["signals"]}


def test_ruptura_de_maximos_con_valores_y_regla():
    closes = [100.0] * 25 + [105.0]
    s = sig(analyze(make_bars(closes)))["breakout"]
    assert s["fired"] is True and s["conditions"][0]["value"] == 105.0 and s["conditions"][1]["value"] == 100.0
    assert "20 cierres anteriores" in s["rule"]
    assert sig(analyze(make_bars([100.0] * 25 + [99.0])))["breakout"]["fired"] is False


def test_recupera_la_media():
    closes = [100.0] * 25 + [95.0, 101.0]  # el cierre previo (95) está bajo la SMA y el actual (101) sobre ella
    assert sig(analyze(make_bars(closes)))["reclaim_ma"]["fired"] is True
    assert sig(analyze(make_bars([100.0] * 27)))["reclaim_ma"]["fired"] is False


def test_sobreventa_y_rebote():
    caida = [100.0 - i for i in range(30)]
    assert sig(analyze(make_bars(caida)))["oversold"]["fired"] is True
    rebote = [*caida, caida[-1] + 1.0]
    r = sig(analyze(make_bars(rebote)))
    assert r["oversold_bounce"]["fired"] is True and r["oversold"]["conditions"][0]["value"] < 30
    assert sig(analyze(make_bars([*caida, caida[-1] - 1.0])))["oversold_bounce"]["fired"] is False  # sigue cayendo


def test_tendencia_alcista_exige_200_velas():
    assert sig(analyze(make_bars([100.0 + i for i in range(220)])))["trend_up"]["fired"] is True
    assert sig(analyze(make_bars([100.0] * 220)))["trend_up"]["fired"] is False
    corto = sig(analyze(make_bars([100.0 + i for i in range(100)])))["trend_up"]
    assert corto["fired"] is None and "200 velas" in corto["reason"]  # no evaluable, no falso


def test_volumen_inusual_y_sin_volumen():
    vols = [100.0] * 25 + [250.0]
    v = sig(analyze(make_bars([100.0] * 26, vols)))["volume_spike"]
    assert v["fired"] is True and v["conditions"][0]["value"] == 2.5
    sin = sig(analyze(make_bars([100.0] * 26)))["volume_spike"]
    assert sin["fired"] is None and "volumen" in sin["reason"]


def test_macd_cruce_coincide_con_la_definicion_en_cada_prefijo():
    """Consistencia: la señal en cada prefijo de la serie es exactamente la condición sobre el histograma."""
    closes = [100 + 8 * math.sin(i / 6) + i * 0.05 for i in range(140)]
    fired_any = False
    for n in range(40, 141):
        res = sig(analyze(make_bars(closes[:n])))["macd_bull_cross"]
        h = ind.macd(closes[:n])["histogram"]
        assert res["fired"] == (h[-2] <= 0 < h[-1])
        fired_any |= bool(res["fired"])
    assert fired_any  # la serie cruza al alza varias veces


def test_pocas_velas_son_no_evaluables_con_motivo():
    res = analyze(make_bars([100.0, 101.0, 102.0, 103.0, 104.0]))
    no_eval = [x for x in res["signals"] if x["id"] in ("breakout", "reclaim_ma", "oversold", "trend_up", "macd_bull_cross")]
    assert no_eval and all(x["fired"] is None and x["reason"] for x in no_eval)
    assert sig(res)["breakout"]["fired"] is None and "21 velas" in sig(res)["breakout"]["reason"]


def test_es_reproducible_y_documenta_parametros():
    bars = make_bars([100 + math.sin(i / 5) * 4 for i in range(120)], [1000.0 + i for i in range(120)])
    a, b = analyze(bars), analyze(bars)
    assert a == b and a["params"]["rsi_days"] == 14 and a["as_of"] == bars[-1]["date"]
    assert "no son pruebas independientes" in a["note"]
    fast = analyze(bars, {"breakout_days": 10})
    assert fast["params"]["breakout_days"] == 10 and "10 cierres anteriores" in sig(fast)["breakout"]["rule"]


def test_plan_por_atr_y_niveles():
    bars = make_bars([100.0] * 60)
    res = analyze(bars)
    plan = res["plan_suggestion"]
    assert plan["entry"] == 100.0 and plan["stop"] == pytest.approx(100 - 2 * res["context"]["atr"])
    assert plan["target"] == pytest.approx(100 + 2 * 2 * res["context"]["atr"]) and "no una recomendación" in plan["basis"]
    sin_hl = [{**b, "high": None, "low": None} for b in bars]
    assert analyze(sin_hl)["plan_suggestion"] is None and sig(analyze(sin_hl))["near_level"]["fired"] is None


@pytest.mark.parametrize(
    "over,msg",
    [({"foo": 1}, "desconocido"), ({"breakout_days": "x"}, "inválido"), ({"breakout_days": 1}, "entre 2 y 400"),
     ({"ema_fast": 200, "ema_slow": 50}, "menor"), ({"rsi_oversold": 120}, "entre 0 y 100"), ({"stop_atr": -1}, "positivos")],
)
def test_parametros_invalidos(over, msg):
    with pytest.raises(SignalError, match=msg):
        resolve_params(over)


def test_entradas_invalidas():
    with pytest.raises(SignalError, match="al menos 2"):
        analyze(make_bars([1.0]))
    bad = make_bars([1.0, 2.0, 3.0])
    bad[1]["close"] = None
    with pytest.raises(SignalError, match="sin cierre"):
        analyze(bad)


# ---------- plan de operación ----------


def test_ejemplo_del_plan_9_4_entrada_100_stop_98_objetivo_104():
    r = evaluate(D(100), D(98), D(104), capital=D(10000), risk_budget=D(50), limits={"max_risk_per_trade_pct": D(1)})
    assert r["shares"] == D(25) and r["risk_per_share"] == D(2) and r["reward_per_share"] == D(4)
    assert r["reward_risk_gross"] == D(2) and r["risk_total"] == D(50) and r["reward_total"] == D(100)
    assert r["position_value"] == D(2500) and r["verdict"] == "PASS" and r["limited_by"] == "riesgo"
    assert any("gaps" in w for w in r["warnings"]) and any("comisiones" in w for w in r["warnings"])


def test_se_bloquea_por_cada_limite():
    base = {"entry": D(100), "stop": D(98), "target": D(104), "capital": D(10000)}
    assert evaluate(**base, risk_budget=D(50))["verdict"] == "PASS"
    rr = evaluate(D(100), D(98), D(102), D(10000), risk_budget=D(50))  # R:R 1.0 < 1.5
    assert rr["verdict"] == "BLOCK" and rr["blocked_by"] == ["min_reward_risk"]
    riesgo = evaluate(**base, risk_budget=D(200))  # 2% del capital > 1%
    assert "max_risk_per_trade_pct" in riesgo["blocked_by"]
    grande = evaluate(**base, risk_budget=D(100), limits={"max_risk_per_trade_pct": D(5), "max_position_pct": D(10)})
    assert grande["blocked_by"] == ["max_position_pct"]  # 50 títulos = 5000 = 50%
    diaria = evaluate(**base, risk_budget=D(50), daily_loss_so_far=D(160))  # 160 + 50 > 2% de 10000
    assert diaria["blocked_by"] == ["max_daily_loss_pct"]


def test_limitado_por_capital_y_tamano_cero():
    r = evaluate(D(100), D(99), D(103), capital=D(1000), risk_budget=D(100), limits={"max_position_pct": D(100)})
    assert r["shares"] == D(10) and r["limited_by"] == "capital"  # el riesgo permitiría 100 títulos
    cero = evaluate(D(500), D(490), D(530), capital=D(300), risk_budget=D(3))
    assert cero["shares"] == D(0) and "size" in cero["blocked_by"]


def test_fraccionarios_deslizamiento_y_comision_empeoran_el_resultado():
    sin = evaluate(D(100), D(98), D(104), D(10000), risk_budget=D(50), shares_scale=2)
    con = evaluate(D(100), D(98), D(104), D(10000), risk_budget=D(50), shares_scale=2, slippage_pct=D("0.5"), commission_per_trade=D(1))
    assert sin["shares"] == D("25.00") and con["shares"] < sin["shares"]
    assert con["reward_risk_net"] < sin["reward_risk_net"] and all("deslizamiento" not in w for w in con["warnings"])


@pytest.mark.parametrize(
    "kw,msg",
    [({"side": "SELL"}, "cortos"), ({"stop": D(101)}, "stop < entrada"), ({"target": D(99)}, "stop < entrada"),
     ({"capital": D(0)}, "mayores que cero"), ({"risk_budget": D(0)}, "presupuesto"), ({"shares_scale": 9}, "shares_scale"),
     ({"slippage_pct": D(-1)}, "negativos")],
)
def test_plan_entradas_invalidas(kw, msg):
    args = {"entry": D(100), "stop": D(98), "target": D(104), "capital": D(10000), "risk_budget": D(50), **kw}
    with pytest.raises(PlanError, match=msg):
        evaluate(**args)
