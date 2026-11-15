"""Liquidez: volumen, tamaño frente al volumen, spread estimado e impacto. Todo con datos sintéticos de propiedades conocidas."""

import math
import random

import pytest

from . import liquidity as lq


def barras(n=60, spread=0.0, volumen=1_000_000, precio=100.0, vol_diaria=0.01, seed=1, hueco=None, pasos=60):
    """Precio eficiente con camino intradía browniano; el máximo cae en el ask y el mínimo en el bid (supuesto de Corwin-Schultz)."""
    rng = random.Random(seed)
    p, out = precio, []
    for i in range(n):
        x, hi, lo = 0.0, 0.0, 0.0
        for _ in range(pasos):
            x += rng.gauss(0, vol_diaria / math.sqrt(pasos))
            hi, lo = max(hi, x), min(lo, x)
        high = p * math.exp(hi) * (1 + spread / 2)
        low = p * math.exp(lo) / (1 + spread / 2)
        p *= math.exp(x)
        v = volumen if hueco is None or i not in hueco else None
        out.append({"open": p, "high": high, "low": low, "close": p, "volume": v})
    return out


def test_el_estimador_de_spread_crece_con_el_spread_real_y_es_cero_sin_el():
    sin = lq.corwin_schultz_bps([b["high"] for b in barras(250, 0.0)], [b["low"] for b in barras(250, 0.0)])
    con = lq.corwin_schultz_bps(
        [b["high"] for b in barras(250, 0.005)], [b["low"] for b in barras(250, 0.005)]
    )
    assert sin["bps"] < 15 and sin["resolvable"] is False and "No distinguible" in sin["note"]
    assert (
        25 <= con["bps"] <= 90 and con["bps"] > sin["bps"] + 20 and con["resolvable"] is True
    )  # spread de 0.5 % ≈ 50 pb
    assert con["se_bps"] > 0


def test_spread_no_evaluable_con_pocos_pares_o_datos_invalidos():
    assert lq.corwin_schultz_bps([10, 10], [9, 9])["bps"] is None
    r = lq.corwin_schultz_bps([None] * 60, [None] * 60)
    assert r["bps"] is None and r["pairs"] == 0
    assert lq.corwin_schultz_bps([5.0] * 60, [6.0] * 60)["bps"] is None  # máximo < mínimo se descarta


def test_el_spread_usa_una_ventana_mas_larga_que_la_del_volumen():
    r = lq.analyze(barras(200, 0.005), window=20)
    assert r["spread_estimate"]["window_days"] == 120 and r["spread_estimate"]["pairs"] >= 100


def test_adv_dolares_y_nivel_de_liquidez():
    r = lq.analyze(barras(60, volumen=2_000_000, precio=100), window=20)
    assert r["evaluable"] and r["adv_shares"] == 2_000_000
    assert 0.9 * 200_000_000 < r["dollar_adv"] < 1.1 * 200_000_000 and r["tier"] == "alta"
    baja = lq.analyze(barras(60, volumen=10_000, precio=20), window=20)
    assert baja["tier"] == "muy baja" and lq.tier(1_000_000) == "baja" and lq.tier(5_000_000) == "media"


def test_tamano_de_la_posicion_frente_al_volumen():
    bars = barras(60, volumen=1_000_000)
    p = lq.analyze(bars, 20, quantity=100_000)["position"]
    assert p["share_of_adv"] == 0.1 and p["size_flag"] == "alto"
    assert p["days_to_trade_at_participation"] == 1.0  # 10 % del volumen al 10 % de participación = 1 día
    assert lq.analyze(bars, 20, quantity=15_000)["position"]["size_flag"] == "visible"
    assert lq.analyze(bars, 20, quantity=100)["position"]["size_flag"] == "bajo"
    assert lq.analyze(bars, 20, quantity=100, participation=0.05)["position"]["participation_assumed"] == 0.05


def test_el_impacto_crece_con_el_tamano_y_con_la_volatilidad():
    chico = lq.analyze(barras(60, vol_diaria=0.01), 20, quantity=10_000)["position"]["impact_sqrt_bps"]
    grande = lq.analyze(barras(60, vol_diaria=0.01), 20, quantity=400_000)["position"]["impact_sqrt_bps"]
    volatil = lq.analyze(barras(60, vol_diaria=0.03), 20, quantity=400_000)["position"]["impact_sqrt_bps"]
    assert 0 < chico < grande < volatil


def test_sin_cantidad_no_hay_bloque_de_posicion():
    assert "position" not in lq.analyze(barras(60), 20)


def test_volumen_faltante_o_pocas_velas_no_se_rellena():
    r = lq.analyze(barras(60, hueco=set(range(40, 60, 2))), 20)  # 10 de 20 sin volumen
    assert r["evaluable"] is False and "volumen" in r["reason"]
    assert lq.analyze(barras(10), 20)["evaluable"] is False
    leve = lq.analyze(barras(60, hueco={55}), 20)  # 1 de 20 (5 %) se tolera y se excluye de la media
    assert leve["evaluable"] and leve["adv_shares"] == 1_000_000


def test_amihud_es_mayor_con_menos_volumen():
    a = lq.analyze(barras(60, volumen=5_000_000), 40)["amihud_x1e9"]
    b = lq.analyze(barras(60, volumen=50_000), 40)["amihud_x1e9"]
    assert b > a > 0


def test_entradas_invalidas():
    for kw in ({"window": 5}, {"window": 500}, {"participation": 0}, {"participation": 0.9},
               {"quantity": -1}, {"quantity": float("nan")}):  # fmt: skip
        with pytest.raises(lq.LiquidityError):
            lq.analyze(barras(60), **{"window": 20, **kw})
    assert "estimación" in " ".join(lq.analyze(barras(60), 20)["caveats"])
