from decimal import Decimal as D

from .piotroski import CRITERIA, evaluate, not_applicable

# Empresa "perfecta": mejora en todo respecto al año anterior.
PREV = {"net_income": D(50), "cfo": D(60), "total_assets": D(1000), "long_term_debt": D(300), "current_assets": D(400),
        "current_liabilities": D(300), "shares_outstanding": D(100), "gross_profit": D(300), "revenue": D(1000)}  # fmt: skip
CUR = {"net_income": D(100), "cfo": D(150), "total_assets": D(1100), "long_term_debt": D(250), "current_assets": D(500),
       "current_liabilities": D(300), "shares_outstanding": D(100), "gross_profit": D(400), "revenue": D(1200)}  # fmt: skip


def by_id(result):
    return {c["id"]: c for c in result["criteria"]}


def test_empresa_perfecta_da_9_de_9_con_evidencia():
    r = evaluate(CUR, PREV, 2025, 2024)
    assert r["score"] == 9 and r["status"] == "completo" and r["evaluable_criteria"] == 9
    c = by_id(r)
    assert c["roa_positive"]["evidence"]["roa"] == D(100) / D(1100)
    assert c["liquidity_up"]["evidence"] == {
        "current_ratio": D(500) / D(300),
        "current_ratio_prev": D(400) / D(300),
    }
    assert [x["id"] for x in r["criteria"]] == [k for k, _ in CRITERIA]


def test_cada_criterio_falla_por_separado():
    cases = {
        "roa_positive": {"net_income": D(-1), "cfo": D(150)},
        "cfo_positive": {"cfo": D(-5), "net_income": D(100)},
        "roa_improved": {"net_income": D(40)},
        "accruals": {"cfo": D(90)},  # CFO 90 < NI 100 (pero positivo)
        "leverage_down": {"long_term_debt": D(400)},
        "liquidity_up": {"current_assets": D(350)},
        "no_dilution": {"shares_outstanding": D(120)},
        "gross_margin_up": {"gross_profit": D(300)},
        "asset_turnover_up": {"revenue": D(900)},
    }
    for crit, over in cases.items():
        r = evaluate({**CUR, **over}, PREV, 2025, 2024)
        assert by_id(r)[crit]["passed"] is False, crit
        assert r["score"] <= 8 and r["status"] == "completo"


def test_dato_faltante_es_no_evaluable_no_cero():
    cur = {k: v for k, v in CUR.items() if k != "long_term_debt"}
    r = evaluate(cur, PREV, 2025, 2024)
    leverage = by_id(r)["leverage_down"]
    assert leverage["passed"] is None and leverage["points"] is None and "Faltan datos" in leverage["reason"]
    assert r["status"] == "parcial" and r["evaluable_criteria"] == 8 and r["score"] == 8  # 8 de 8 evaluables


def test_sin_anio_anterior_solo_cuentan_los_criterios_de_un_solo_anio():
    r = evaluate(CUR, None)
    ev = {c["id"] for c in r["criteria"] if c["passed"] is not None}
    assert ev == {"roa_positive", "cfo_positive", "accruals"} and r["status"] == "parcial"
    assert "anterior" in by_id(r)["roa_improved"]["reason"]


def test_anios_no_consecutivos_no_se_comparan():
    r = evaluate(CUR, PREV, 2025, 2023)  # falta 2024
    assert r["evaluable_criteria"] == 3 and "consecutivo" in by_id(r)["roa_improved"]["reason"]


def test_sin_ningun_dato_no_hay_puntaje():
    r = evaluate({}, None)
    assert r["score"] is None and r["status"] == "no_evaluable" and r["evaluable_criteria"] == 0


def test_denominador_cero_no_rompe():
    r = evaluate({**CUR, "total_assets": D(0)}, PREV, 2025, 2024)
    assert by_id(r)["roa_positive"]["passed"] is None  # no se puede dividir entre cero


def test_empate_no_cuenta_como_mejora():
    r = evaluate(PREV, PREV, 2025, 2024)
    c = by_id(r)
    assert c["roa_improved"]["passed"] is False and c["gross_margin_up"]["passed"] is False
    assert c["no_dilution"]["passed"] is True  # mismas acciones = sin emisión
    assert c["leverage_down"]["passed"] is False  # misma deuda: no disminuyó


def test_sector_excluido():
    r = not_applicable("Sector financiero: el F-Score original lo excluye.")
    assert r["status"] == "no_aplica" and r["score"] is None
