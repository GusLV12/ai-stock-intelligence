from datetime import date, timedelta

import pytest

from .backtest import BacktestConfig, CostModel, run_backtest

D0 = date(2025, 1, 1)
N = 30


def serie(prices, volume=None):
    return [{"date": D0 + timedelta(days=i), "close": p, "volume": volume} for i, p in enumerate(prices)]


def dias(a, b):
    return [D0 + timedelta(days=i) for i in range(a, b + 1)]


def scores(dates, favorito):
    return {d: {k: (1.0 if k == favorito else 0.0) for k in (1, 2)} for d in dates}


SIN_COSTOS = CostModel(0, 0, 0)


def cfg(**kw):
    base = {"top_k": 1, "rebalance_days": 1000, "costs": SIN_COSTOS, "max_adv_participation": 1.0}
    return BacktestConfig(**{**base, **kw})


def test_costos_se_descuentan_a_mano():
    s = {1: serie([100.0] * N), 2: serie([100.0] * N)}
    ds = dias(5, 20)
    r = run_backtest(s, scores(ds, 1), ds, cfg(costs=CostModel(1, 2, 2)))  # 5 pb por lado
    assert r["strategy"]["total_return"] == pytest.approx(-0.00049975, abs=1e-7)
    assert r["strategy"]["cost_paid"] == pytest.approx(49.975, abs=1e-3) and r["strategy"]["rebalances"] == 1


def test_ejecuta_con_retraso_y_no_captura_el_salto_previo():
    precios_a = [100.0] * 10 + [120.0] * 20  # el salto ocurre entre t=9 y t=10
    s = {1: serie(precios_a), 2: serie([100.0] * N)}
    ds = dias(9, 20)
    con_retraso = run_backtest(s, scores(ds, 1), ds, cfg(delay=1))
    sin_retraso = run_backtest(s, scores(ds, 1), ds, cfg(delay=0))
    assert con_retraso["strategy"]["total_return"] == pytest.approx(
        0.0, abs=1e-12
    )  # entra a 120: ya no hay ganancia
    assert sin_retraso["strategy"]["total_return"] == pytest.approx(
        0.20
    )  # (irreal) entrar al cierre de la señal
    assert con_retraso["config"]["period"][0] == str(D0 + timedelta(days=10))


def test_elegir_al_ganador_supera_al_universo_igual():
    s = {1: serie([100.0 * 1.01**i for i in range(N)]), 2: serie([100.0] * N)}
    ds = dias(5, 20)
    r = run_backtest(s, scores(ds, 1), ds, cfg())
    assert r["strategy"]["total_return"] > r["benchmark_buy_hold"]["total_return"] > 0
    assert r["excess_vs_equal_weight"] > 0
    assert (
        r["strategy"]["max_drawdown"] == 0.0
        and r["strategy"]["sharpe"] is None
        or r["strategy"]["sharpe"] > 0
    )


def test_rebalanceo_genera_rotacion_y_costo():
    s = {1: serie([100.0] * N), 2: serie([100.0] * N)}
    ds = dias(5, 20)
    alterna = {d: {1: float(i % 2), 2: float((i + 1) % 2)} for i, d in enumerate(ds)}
    con = run_backtest(s, alterna, ds, cfg(rebalance_days=1, costs=CostModel(5, 0, 0)))
    assert con["strategy"]["rebalances"] == len(ds) and con["strategy"]["turnover_one_way_annual"] > 10
    assert (
        con["strategy"]["total_return"] < -0.002
    )  # los costos se comen el capital aunque el precio no se mueva
    assert con["benchmark_buy_hold"]["cost_paid"] < con["strategy"]["cost_paid"]


def test_limite_de_liquidez_deja_efectivo():
    s = {1: serie([100.0] * N, volume=10), 2: serie([100.0] * N, volume=10)}  # ADV = 1000 en dinero
    s[1] = serie([100.0 * 1.01**i for i in range(N)], volume=10)
    ds = dias(25 - 5, 25)
    r = run_backtest(s, scores(ds, 1), ds, cfg(max_adv_participation=0.05, capital=100_000.0))
    assert r["liquidity_checked"] is True and r["strategy"]["liquidity_capped_positions"] >= 1
    assert abs(r["strategy"]["total_return"]) < 1e-3  # casi todo el capital quedó en efectivo


def test_sin_volumen_se_declara_que_no_se_verifico():
    s = {1: serie([100.0] * N), 2: serie([100.0] * N)}
    ds = dias(5, 20)
    r = run_backtest(s, scores(ds, 1), ds, cfg())
    assert r["liquidity_checked"] is False and any("liquidez" in n for n in r["notes"])


def test_pocas_fechas_falla_claro():
    s = {1: serie([100.0] * N), 2: serie([100.0] * N)}
    with pytest.raises(ValueError):
        run_backtest(s, scores(dias(5, 5), 1), dias(5, 5), cfg())
