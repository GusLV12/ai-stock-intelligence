from datetime import date
from decimal import Decimal as D

import pytest

from .engine import (
    CalcError,
    cagr,
    cagr_between_dates,
    compare_scenarios,
    dividend_total_return,
    fx_adjusted_return,
    irr,
    max_buy_price,
    project,
    real_return,
    twr,
)


def close(a, b, tol="1e-9"):
    return abs(a - b) < D(tol)


def test_cagr_ejemplo_del_plan_10000_a_16105_en_5_anios_es_10_por_ciento():
    r = cagr(D(10000), D(16105), D(5))
    assert close(r["cagr"], D("0.1"), "1e-5")  # 1.1^5 = 1.61051
    assert close(r["total_return"], D("0.6105"))


def test_cagr_duplicar_en_un_anio_y_anios_fraccionarios():
    assert close(cagr(D(100), D(200), D(1))["cagr"], D(1))
    assert close(cagr(D(100), D(121), D(2))["cagr"], D("0.1"))
    assert close(cagr(D(100), D(100), D("0.5"))["cagr"], D(0))
    # raíz no entera: (4)^(1/2) - 1 = 1
    assert close(cagr(D(100), D(400), D(2))["cagr"], D(1))


def test_cagr_por_fechas_usa_dias_entre_365():
    r = cagr_between_dates(D(100), D(110), date(2025, 1, 1), date(2026, 1, 1))
    assert close(r["cagr"], D("0.1")) and r["day_count"] == "días/365"
    with pytest.raises(CalcError, match="posterior"):
        cagr_between_dates(D(1), D(2), date(2026, 1, 1), date(2025, 1, 1))


@pytest.mark.parametrize(
    "args,msg",
    [((D(0), D(1), D(1)), "mayores que cero"), ((D(1), D(-1), D(1)), "mayores que cero"),
     ((D(1), D(2), D(0)), "mayor que 0"), ((D(1), D(2), D(51)), "no exceder")],
)  # fmt: skip
def test_cagr_entradas_invalidas(args, msg):
    with pytest.raises(CalcError, match=msg):
        cagr(*args)


def test_retorno_real_y_fx():
    assert close(real_return(D("0.10"), D("0.04"))["real_return"], D("1.10") / D("1.04") - 1)
    assert close(real_return(D("0.05"), D("0.05"))["real_return"], D(0))
    fx = fx_adjusted_return(D("0.10"), D(18), D("19.8"))  # peso se deprecia 10%
    assert close(fx["return_in_mxn"], D("1.1") * D("1.1") - 1) and close(fx["fx_component"], D("0.1"))
    with pytest.raises(CalcError):
        fx_adjusted_return(D("0.1"), D(0), D(1))


def test_twr_neutraliza_flujos_externos():
    # 100 -> 110 (+10%); se aportan 100 al inicio del 2.º periodo: base 210 -> 231 (+10%). TWR = 1.1*1.1-1 = 21%.
    r = twr(
        [
            {"begin_value": D(100), "end_value": D(110)},
            {"begin_value": D(110), "end_value": D(231), "flow": D(100)},
        ]
    )
    assert close(r["twr"], D("0.21"))
    with pytest.raises(CalcError):
        twr([])


def test_irr_con_solucion_conocida():
    # -1000 hoy, +1100 en 1 año => 10%
    assert close(irr([(D(0), D(-1000)), (D(1), D(1100))]), D("0.1"), "1e-9")
    # Flujos intermedios: -100, +10 en t1, +110 en t2 => 10%
    assert close(irr([(D(0), D(-100)), (D(1), D(10)), (D(2), D(110))]), D("0.1"), "1e-9")
    assert irr([(D(0), D(-100)), (D(1), D(-5))]) is None and irr([(D(0), D(-1))]) is None


def test_dividendos_reinvertidos_son_distintos_de_sumarlos_al_precio():
    # 10 títulos a 100, final 100, un dividendo de 5/título reinvertido a 100: 10.5 títulos => +5%.
    r = dividend_total_return(D(10), D(100), D(100), [{"per_share": D(5), "reinvest_price": D(100)}])
    assert r["price_return"] == 0 and close(r["total_return_reinvested_gross"], D("0.05"))
    assert close(r["total_return_cash_gross"], D("0.05"))
    # Dos dividendos: el 2.º se paga sobre más títulos (interés compuesto) => reinversión > efectivo
    two = dividend_total_return(D(10), D(100), D(100), [{"per_share": D(5), "reinvest_price": D(100)}] * 2)
    assert two["total_return_reinvested_gross"] > two["total_return_cash_gross"]
    assert close(two["total_return_reinvested_gross"], D("1.05") ** 2 - 1)


def test_dividendos_con_retencion():
    r = dividend_total_return(
        D(10), D(100), D(100), [{"per_share": D(10), "reinvest_price": D(100)}], withholding=D("0.30")
    )
    assert close(r["total_return_reinvested_net"], D("0.07"))  # 10% × (1 − 30%)
    with pytest.raises(CalcError, match="retención"):
        dividend_total_return(D(1), D(1), D(1), [], withholding=D(1))
    with pytest.raises(CalcError, match="reinversión"):
        dividend_total_return(D(1), D(1), D(1), [{"per_share": D(1), "reinvest_price": D(0)}])


def test_proyeccion_con_multiplos_ejemplo_manual():
    # EPS 5, crece 10% por 5 años, múltiplo de salida 20, compra a 100 (P/E de entrada 20).
    r = project(D(100), D(5), D("0.10"), D(5), D(20))
    assert close(r["fundamental_final"], D(5) * D("1.1") ** 5)
    assert close(r["price_future"], D(100) * D("1.1") ** 5)
    assert close(r["cagr_price"], D("0.1"), "1e-9") and close(r["multiple_change"], D(0))
    assert "no es una predicción" in r["scenario_note"] and r["warnings"] == []


def test_compresion_de_multiplo_resta_rendimiento_y_se_advierte():
    flat = project(D(100), D(5), D("0.10"), D(5), D(20))
    comp = project(D(100), D(5), D("0.10"), D(5), D(15))
    assert comp["cagr_price"] < flat["cagr_price"] and comp["cagr_price"] < D("0.10")
    assert any("compresión" in w for w in comp["warnings"])
    assert any("expansión" in w for w in project(D(100), D(5), D("0.10"), D(5), D(30))["warnings"])


def test_dilucion_reduce_el_crecimiento_por_accion():
    sin = project(D(100), D(5), D("0.10"), D(5), D(20))
    con = project(D(100), D(5), D("0.10"), D(5), D(20), dilution=D("0.02"))
    assert con["effective_growth"] < sin["effective_growth"] and close(
        con["effective_growth"], D("1.10") * D("0.98") - 1
    )
    assert any("dilución" in w for w in con["warnings"])


def test_tir_total_con_dividendos():
    # Compra 100, sin crecimiento de precio, dividendo 5 por 3 años: TIR = 5% exacto.
    r = project(D(100), D(5), D(0), D(3), D(20), dividends_per_share=[D(5), D(5), D(5)])
    assert close(r["total_irr"], D("0.05"), "1e-9") and close(r["cagr_price"], D(0))
    with pytest.raises(CalcError, match="un dividendo por año"):
        project(D(100), D(5), D(0), D(3), D(20), dividends_per_share=[D(5)])


def test_comparador_de_escenarios_no_mezcla_ni_pondera():
    base = {
        "buy_price": D(100),
        "fundamental_initial": D(5),
        "growth": D("0.10"),
        "years": D(5),
        "exit_multiple": D(20),
    }
    out = compare_scenarios(
        base, {"bear": {"growth": D("0.02"), "exit_multiple": D(15)}, "bull": {"growth": D("0.15")}}
    )
    c = out["cagr_price"]
    assert c["bear"] < c["base"] < c["bull"] and set(c) == {"base", "bear", "bull"}
    assert "probabilidad" in out["scenario_note"]


def test_precio_maximo_por_rentabilidad_objetivo():
    # Valor futuro 161.05 en 5 años a 10% objetivo => 100 hoy.
    r = max_buy_price(D("161.051"), D(5), D("0.10"))
    assert close(r["max_price_by_return"], D(100), "1e-6")
    assert "conservative_max_price" not in r and any("Sin margen" in w for w in r["warnings"])


def test_precio_maximo_con_dividendos_descuenta_cada_flujo_en_su_fecha():
    # Dividendo 5 por 2 años + 100 al final, objetivo 5% => 5/1.05 + 5/1.05^2 + 100/1.05^2.
    r = max_buy_price(D(100), D(2), D("0.05"), dividends_per_share=[D(5), D(5)])
    expected = D(5) / D("1.05") + D(5) / D("1.05") ** 2 + D(100) / D("1.05") ** 2
    assert close(r["max_price_by_return"], expected, "1e-9")


def test_precio_conservador_exige_ambos_criterios():
    r = max_buy_price(D(200), D(5), D("0.10"), estimated_value=D(120), margin_of_safety=D("0.25"))
    assert close(r["max_price_by_margin_of_safety"], D(90)) and r["conservative_max_price"] == min(
        r["max_price_by_return"], D(90)
    )
    with pytest.raises(CalcError, match="indica el valor estimado"):
        max_buy_price(D(200), D(5), D("0.10"), estimated_value=D(120))
    with pytest.raises(CalcError):
        max_buy_price(D(0), D(5), D("0.1"))
    with pytest.raises(CalcError):
        max_buy_price(D(100), D(5), D("0.1"), estimated_value=D(120), margin_of_safety=D(1))
