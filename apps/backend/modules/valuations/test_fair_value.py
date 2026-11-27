from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from .engine import ValuationError
from .fair_value import asi_fair_value_v1, plain

DCF = {
    "fcff_base": 100, "growth": 0, "years": 5, "wacc": "0.10", "terminal_growth": 0,
    "net_debt": 0, "diluted_shares": 10,
}  # fmt: skip
# Con esos datos el DCF vale 100 por acción (perpetuidad de 100 / 10%, 10 acciones).


def run(**kw):
    base = {
        "instrument_class": "STOCK",
        "market_price": 80,
        "currency": "USD",
        "financials_as_of": date.today().isoformat(),
    }
    return asi_fair_value_v1({**base, **kw})


def test_fecha_financiera_futura_se_rechaza():
    with pytest.raises(ValuationError, match="futura"):
        run(dcf=DCF, financials_as_of=(date.today() + timedelta(days=1)).isoformat())


def test_dcf_con_escenarios_y_margen_de_seguridad():
    r = run(dcf={**DCF, "scenarios": {"bear": {"wacc": "0.12"}, "bull": {"growth": 0.04}}})
    m = r["methods"][0]
    assert m["method"] == "dcf" and m["applicable"] and round(m["value_per_share"], 6) == D(100)
    sc = r["scenario_range"]
    assert sc["bear"] < sc["base"] < sc["bull"]
    assert r["central_value"] == m["value_per_share"] and r["compatible"] is True
    assert round(r["margin_of_safety_base"], 6) == D("0.2")  # (100 − 80) / 100
    assert round(r["upside_to_value"], 6) == D("0.25")
    assert r["reverse_dcf"]["solved"] is True and "sensitivity" in m
    assert r["assumptions_version"] == "valuation-v1" and "asesoría" in r["disclaimer"]


def test_varios_metodos_compatibles_dan_valor_central_por_mediana():
    r = run(
        dcf=DCF,
        ddm={"cost_of_equity": "0.10", "d1": 6, "growth": "0.0"},  # 60
        relative=[{"method": "pe", "metric": 8, "multiples": [10, 12, 14]}],  # 8 × 12 = 96
    )
    vals = sorted(m["value_per_share"] for m in r["methods"] if m["applicable"])
    assert [round(v) for v in vals] == [60, 96, 100]
    assert r["compatible"] is False and r["central_value"] is None  # 100/60 > 1.5
    assert r["margin_of_safety_base"] is None
    assert any("difieren mucho" in w for w in r["warnings"])


def test_metodos_cercanos_son_compatibles():
    r = run(dcf=DCF, relative=[{"method": "pe", "metric": 8, "multiples": [12]}])  # 100 y 96
    assert r["compatible"] is True and round(r["central_value"], 6) == D(98)


def test_banco_no_usa_dcf_ni_multiplos_de_ev():
    r = run(
        sector_kind="bank", dcf=DCF,
        ddm={"cost_of_equity": "0.11", "d1": 3, "growth": "0.03"},
        relative=[{"method": "p_b", "metric": 20, "multiples": [1.2, 1.4]},
                  {"method": "ev_ebitda", "metric": 100, "multiples": [8], "diluted_shares": 10}],
    )  # fmt: skip
    by = {m["method"]: m for m in r["methods"]}
    assert by["dcf"]["applicable"] is False and "bancos" in by["dcf"]["reason"]
    assert by["ddm"]["applicable"] and by["relative:p_b"]["applicable"]
    assert by["relative:ev_ebitda"]["applicable"] is False


@pytest.mark.parametrize("cls", ["ETF", "CRYPTO"])
def test_etf_y_cripto_no_son_evaluables_y_no_dan_cifra(cls):
    r = run(instrument_class=cls, dcf=DCF, ddm={"cost_of_equity": "0.1", "d1": 1, "growth": 0},
            relative=[{"method": "pe", "metric": 5, "multiples": [10]}])  # fmt: skip
    assert r["evaluable"] is False and all(not m["applicable"] for m in r["methods"])
    assert r["central_value"] is None and r["valuation_range"] is None
    assert any("No evaluable" in w for w in r["warnings"])


def test_adr_advierte_ratio_y_moneda():
    r = run(instrument_class="ADR", dcf=DCF)
    assert any("ADR" in w and "ratio" in w for w in r["methods"][0]["warnings"])
    assert any("ratio ADR" in w for w in r["warnings"])


def test_metodo_inaplicable_se_explica_y_los_demas_siguen():
    r = run(dcf={**DCF, "fcff_base": -5}, relative=[{"method": "pe", "metric": 8, "multiples": [12]}])
    assert r["methods"][0]["applicable"] is False and r["evaluable"] is True
    assert any("Un solo método" in w for w in r["warnings"])


def test_vigencia_de_los_estados_financieros():
    assert any("desactualizados" in w for w in run(dcf=DCF, financials_as_of="2020-01-01")["warnings"])
    sin = asi_fair_value_v1({"instrument_class": "STOCK", "market_price": 80, "dcf": DCF})
    assert any("fecha de los estados financieros" in w for w in sin["warnings"])
    with pytest.raises(ValuationError, match="fecha ISO"):
        run(dcf=DCF, financials_as_of="ayer")


@pytest.mark.parametrize(
    "bad,msg",
    [
        ({"instrument_class": "BONO", "dcf": DCF}, "instrument_class"),
        ({"sector_kind": "otro", "dcf": DCF}, "sector_kind"),
        ({"market_price": 0, "dcf": DCF}, "market_price"),
        ({"market_price": "abc", "dcf": DCF}, "inválido"),
        ({"market_price": True, "dcf": DCF}, "requerido"),
        ({}, "al menos un bloque"),
        ({"dcf": {**DCF, "wacc": "0.02", "terminal_growth": "0.03"}}, "WACC > g"),
        ({"dcf": {**DCF, "diluted_shares": None}}, "diluted_shares"),
        ({"dcf": {**DCF, "growth": "NaN"}}, "inválido"),
    ],
)
def test_entradas_invalidas(bad, msg):
    with pytest.raises(ValuationError, match=msg):
        run(**bad)


def test_resultado_serializable_sin_perder_precision():
    out = plain(run(dcf=DCF))
    assert out["methods"][0]["value_per_share"] == "100.000000"
    import json

    json.dumps(out)
