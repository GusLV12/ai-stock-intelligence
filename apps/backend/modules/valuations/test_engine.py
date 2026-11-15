from decimal import Decimal as D

import pytest

from .engine import (
    ValuationError,
    dcf,
    ddm_gordon,
    ddm_two_stage,
    margin_of_safety,
    relative_value,
    reverse_dcf,
    sensitivity_wacc_g,
)

# Caso con respuesta conocida: FCFF constante de 100, sin crecimiento y WACC 10% => perpetuidad = 1000 exactos.
FLAT = {
    "fcff_base": D(100), "growth": D(0), "years": 5, "wacc": D("0.10"), "terminal_growth": D(0),
    "net_debt": D(0), "diluted_shares": D(10),
}  # fmt: skip


def test_dcf_perpetuidad_plana_vale_mil():
    r = dcf(**FLAT)
    assert round(r["enterprise_value"], 8) == D(1000)  # verificable a mano: 100 / 0.10
    assert round(r["value_per_share"], 8) == D(100)
    assert round(r["pv_explicit_flows"], 4) == D("379.0787")  # 100 × anualidad(5, 10%)
    assert round(r["pv_terminal_value"], 4) == D("620.9213")  # 1000 / 1.1^5


def test_dcf_con_crecimiento_coincide_con_calculo_manual():
    # FCFF0=100, g=5% por 2 años, WACC 10%, g terminal 2%; deuda neta 50; 10 acciones.
    r = dcf(D(100), D("0.05"), 2, D("0.10"), D("0.02"), D(50), D(10))
    f1, f2 = D(105), D("110.25")
    pv = f1 / D("1.1") + f2 / D("1.21")
    tv = f2 * D("1.02") / D("0.08") / D("1.21")
    assert round(r["enterprise_value"], 8) == round(pv + tv, 8)
    assert round(r["value_per_share"], 8) == round((pv + tv - 50) / 10, 8)


def test_dcf_puente_de_ev_a_capital():
    r = dcf(**{**FLAT, "net_debt": D(200), "other_claims": D(100), "non_operating_assets": D(50)})
    assert round(r["equity_value"], 6) == D(750)  # 1000 − 200 − 100 + 50


def test_dcf_crecimientos_por_anio():
    r = dcf(**{**FLAT, "growth": [D("0.10"), D("0.05"), D(0), D(0), D(0)]})
    assert round(r["flows"][0]["fcff"], 6) == D(110) and round(r["flows"][1]["fcff"], 6) == D("115.5")
    with pytest.raises(ValuationError, match="un valor por cada año"):
        dcf(**{**FLAT, "growth": [D("0.1")]})


@pytest.mark.parametrize(
    "override,msg",
    [
        ({"wacc": D("0.02"), "terminal_growth": D("0.03")}, "WACC > g"),
        ({"wacc": D("0.05"), "terminal_growth": D("0.05")}, "WACC > g"),
        ({"diluted_shares": D(0)}, "acciones diluidas"),
        ({"years": 0}, "entre 1 y 30"),
        ({"years": 31}, "entre 1 y 30"),
        ({"wacc": D(0)}, "WACC fuera de rango"),
    ],
)
def test_dcf_supuestos_invalidos(override, msg):
    with pytest.raises(ValuationError, match=msg):
        dcf(**{**FLAT, **override})


def test_dcf_con_flujo_negativo_no_aplica_en_vez_de_inventar():
    r = dcf(**{**FLAT, "fcff_base": D(-50)})
    assert r["applicable"] is False and "no es fiable" in r["reason"]


def test_dcf_advertencias_de_valor_terminal_y_g_alta():
    r = dcf(**{**FLAT, "terminal_growth": D("0.05"), "wacc": D("0.08")})
    assert any("valor terminal pesa" in w for w in r["warnings"])
    assert any("supera 4%" in w for w in r["warnings"])


def test_sensibilidad_wacc_g():
    s = sensitivity_wacc_g(FLAT)
    center = s["value_per_share"][2][2]
    assert round(center, 8) == D(100)  # al centro, el caso base
    assert s["value_per_share"][0][2] > center > s["value_per_share"][4][2]  # menor WACC => mayor valor
    # combinaciones con WACC <= g quedan en None
    tight = sensitivity_wacc_g({**FLAT, "wacc": D("0.03"), "terminal_growth": D("0.025")})
    assert any(v is None for row in tight["value_per_share"] for v in row)


def test_ddm_gordon_ejemplo_conocido():
    r = ddm_gordon(D(2), D("0.10"), D("0.04"))
    assert round(r["value_per_share"], 8) == round(D(2) / D("0.06"), 8)  # 33.3333…
    with pytest.raises(ValuationError, match="r > g"):
        ddm_gordon(D(2), D("0.04"), D("0.05"))
    assert ddm_gordon(D(0), D("0.10"), D("0.04"))["applicable"] is False


def test_ddm_dos_etapas_con_etapa_alta_igual_a_estable_equivale_a_gordon():
    two = ddm_two_stage(D(2), D("0.04"), 5, D("0.04"), D("0.10"))
    assert round(two["value_per_share"], 6) == round(D(2) * D("1.04") / D("0.06"), 6)
    higher = ddm_two_stage(D(2), D("0.12"), 5, D("0.04"), D("0.10"))
    assert higher["value_per_share"] > two["value_per_share"]
    assert ddm_two_stage(D(0), D("0.1"), 3, D("0.03"), D("0.09"))["applicable"] is False


def test_relativo_pe_cuartiles_y_advertencias():
    r = relative_value("pe", D(5), [D(10), D(12), D(14), D(16)])
    assert r["multiple_median"] == D(13) and r["value_per_share"] == D(65)
    assert r["value_low"] < r["value_per_share"] < r["value_high"]
    assert relative_value("pe", D(5), [D(10), D(12)])["warnings"][0].startswith("Menos de 3")


def test_relativo_no_aplica_con_utilidad_negativa_y_descarta_multiplos_invalidos():
    r = relative_value("pe", D(-1), [D(10)])
    assert r["applicable"] is False and "no aplica" in r["reason"]
    assert relative_value("p_s", D(2), [D(-3), D(0), D(4)])["value_per_share"] == D(8)  # solo cuenta el 4
    with pytest.raises(ValuationError, match="al menos un múltiplo"):
        relative_value("pe", D(5), [D(-1)])
    with pytest.raises(ValuationError, match="desconocido"):
        relative_value("peg", D(5), [D(10)])


def test_relativo_ev_ebitda_pasa_por_deuda_neta_y_acciones():
    r = relative_value("ev_ebitda", D(100), [D(10)], net_debt=D(200), diluted_shares=D(10))
    assert r["value_per_share"] == D(80)  # (100×10 − 200) / 10
    with pytest.raises(ValuationError, match="acciones diluidas"):
        relative_value("ev_ebitda", D(100), [D(10)])


def test_reverse_dcf_recupera_el_crecimiento_conocido():
    params = {k: v for k, v in FLAT.items() if k != "growth"}
    price = dcf(**{**params, "growth": D("0.07")})["value_per_share"]
    r = reverse_dcf(price, params)
    assert r["solved"] and abs(r["implied_growth"] - D("0.07")) < D("0.0001")


def test_reverse_dcf_sin_solucion_y_sin_flujo_positivo():
    params = {k: v for k, v in FLAT.items() if k != "growth"}
    out = reverse_dcf(D(100000), params)
    assert out["applicable"] and out["solved"] is False and "ningún crecimiento" in out["reason"]
    assert reverse_dcf(D(10), {**params, "fcff_base": D(-1)})["applicable"] is False
    with pytest.raises(ValuationError):
        reverse_dcf(D(0), params)


def test_margen_de_seguridad_del_plan():
    m = margin_of_safety(D(120), D(90))  # ejemplo de la sección 5.1
    assert m["margin_of_safety"] == D("0.25") and round(m["upside_to_value"], 4) == D("0.3333")
    assert margin_of_safety(D(0), D(10))["margin_of_safety"] is None
