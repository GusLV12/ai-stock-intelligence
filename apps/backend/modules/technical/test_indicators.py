import math

import pytest

from . import indicators as ind


def approx(x, **kw):
    return pytest.approx(x, rel=kw.get("rel", 1e-9), abs=kw.get("abs", 1e-9))


def test_sma_alineada_con_calentamiento():
    assert ind.sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    assert ind.sma([5], 3) == [None]
    with pytest.raises(ValueError):
        ind.sma([1], 0)


def test_ema_sembrada_con_sma_ejemplo_manual():
    # EMA(3) de 1..5: semilla SMA(1,2,3)=2; alfa 0.5 -> 4*.5+2*.5=3; 5*.5+3*.5=4
    assert ind.ema([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    assert ind.ema([1, 2], 3) == [None, None]
    assert ind.ema([7.0] * 10, 4)[-1] == approx(7.0)


def test_rsi_wilder_ejemplo_manual():
    # n=3, cierres 10,11,12,11,12: cambios +1,+1,-1,+1.
    # RSI[3]: gan=(1+1+0)/3, pér=(0+0+1)/3 => RS=2 => 66.667; RSI[4]: gan=(2/3*2+1)/3, pér=(1/3*2+0)/3 => RS=3.5 => 77.778
    r = ind.rsi([10, 11, 12, 11, 12], 3)
    assert r[:3] == [None, None, None] and r[3] == approx(100 - 100 / 3) and r[4] == approx(100 - 100 / 4.5)


def test_rsi_casos_limite():
    assert ind.rsi(list(range(1, 30)), 14)[-1] == 100.0  # solo sube
    assert ind.rsi([5.0] * 30, 14)[-1] == 50.0  # no se movió
    assert ind.rsi(list(range(30, 1, -1)), 14)[-1] == approx(0.0)  # solo baja
    assert ind.rsi([1, 2, 3], 14) == [None, None, None]


def test_rsi_siempre_entre_0_y_100():
    import random

    rng = random.Random(5)
    closes = [100.0]
    for _ in range(300):
        closes.append(closes[-1] * (1 + rng.uniform(-0.05, 0.05)))
    assert all(0 <= v <= 100 for v in ind.rsi(closes) if v is not None)


def test_macd_constante_es_cero_y_coincide_con_la_definicion():
    flat = ind.macd([10.0] * 60)
    assert all(v == approx(0.0) for v in flat["macd"] if v is not None)
    assert all(v == approx(0.0) for v in flat["histogram"] if v is not None)
    closes = [100 + i * 0.7 + math.sin(i / 3) * 2 for i in range(80)]
    m = ind.macd(closes)
    ef, es = ind.ema(closes, 12), ind.ema(closes, 26)
    assert m["macd"][25] == approx(ef[25] - es[25]) and m["macd"][24] is None
    assert m["signal"][33] is not None and m["signal"][32] is None  # señal: 9 valores de MACD desde el índice 25
    assert m["histogram"][40] == approx(m["macd"][40] - m["signal"][40])
    with pytest.raises(ValueError):
        ind.macd(closes, 26, 12)


def test_atr_de_wilder_con_un_gap_ejemplo_manual():
    # n=2. TR: 2, 2, 2, y la 4.ª barra salta (h15,l13,c14; cierre previo 11) => TR = max(2, 4, 2) = 4
    highs, lows, closes = [10, 11, 12, 15], [8, 9, 10, 13], [9, 10, 11, 14]
    assert ind.true_range(highs, lows, closes) == [2, 2, 2, 4]
    a = ind.atr(highs, lows, closes, 2)
    assert a[0] is None and a[1] == 2.0 and a[2] == 2.0 and a[3] == 3.0  # (2*1 + 4)/2


def test_bollinger_poblacional_ejemplo_manual():
    b = ind.bollinger([1, 2, 3], 3, 2.0)
    sd = math.sqrt(2 / 3)
    assert b["middle"][2] == 2.0 and b["upper"][2] == approx(2 + 2 * sd) and b["lower"][2] == approx(2 - 2 * sd)
    flat = ind.bollinger([4.0] * 25)
    assert flat["upper"][-1] == flat["lower"][-1] == flat["middle"][-1] == 4.0


def test_volumen_relativo_usa_solo_los_dias_anteriores():
    v = [100.0] * 20 + [300.0, 100.0]
    r = ind.relative_volume(v, 20)
    assert r[20] == 3.0 and r[21] == approx(100 / ((100.0 * 19 + 300) / 20))
    assert r[19] is None and ind.relative_volume([None] * 25, 20)[24] is None
    assert ind.relative_volume([0.0] * 21, 20)[20] is None  # media cero: indefinido


def test_niveles_agrupan_pivotes_cercanos_y_cuentan_toques():
    # Zigzag con techos repetidos cerca de 110 (110, 109.6, 110.4) y pisos cerca de 90.
    highs, lows = [], []
    for cycle in range(4):
        top = [110, 109.6, 110.4, 110.1][cycle]
        for k in range(12):
            h = top if k == 6 else top - 6 - abs(k - 6) * 0.4
            highs.append(h)
            lows.append(h - 18 - (0 if k != 3 else 0.2))
    last = 100.0
    levels = ind.key_levels(highs, lows, last, wing=4, tolerance=0.01)
    res = [lv for lv in levels if lv["kind"] == "resistance" and 109 <= lv["price"] <= 111]
    assert res and res[0]["touches"] >= 3
    assert all(lv["kind"] == ("support" if lv["price"] < last else "resistance") for lv in levels)
    assert levels == sorted(levels, key=lambda lv: abs(lv["price"] - last))  # ordenados por cercanía


def test_determinismo():
    closes = [100 + math.sin(i / 4) * 5 + i * 0.1 for i in range(120)]
    assert ind.rsi(closes) == ind.rsi(closes) and ind.macd(closes) == ind.macd(closes)
