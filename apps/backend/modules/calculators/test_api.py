import csv
import io

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient


@pytest.fixture
def client(db):
    c = APIClient()
    c.force_authenticate(get_user_model().objects.create_user("ana", password="x"))
    return c


def cagr(client, body, fmt=""):
    return client.post("/api/v1/calculators/cagr", body, format="json", QUERY_STRING=fmt.lstrip("?"))


def test_cagr_historico_y_por_fechas(client):
    r = cagr(client, {"mode": "historical", "initial": 10000, "final": 16105, "years": 5})
    assert r.status_code == 200, r.content
    assert r.json()["result"]["cagr"].startswith("0.0999") or r.json()["result"]["cagr"].startswith("0.1000")
    assert r.json()["inputs"]["initial"] == 10000 and "computed_at" in r.json()
    r = cagr(
        client,
        {
            "mode": "historical",
            "initial": "100",
            "final": "110",
            "start_date": "2025-01-01",
            "end_date": "2026-01-01",
        },
    )
    assert r.json()["result"]["cagr"] == "0.100000"


def test_proyeccion_con_escenarios(client):
    body = {"mode": "projected", "buy_price": 100, "fundamental_initial": 5, "growth": "0.10", "years": 5, "exit_multiple": 20,
            "scenarios": {"bear": {"growth": "0.02", "exit_multiple": 15}, "bull": {"growth": "0.15"}}}  # fmt: skip
    r = cagr(client, body)
    assert r.status_code == 200, r.content
    c = r.json()["result"]["cagr_price"]
    assert float(c["bear"]) < float(c["base"]) < float(c["bull"])
    assert "probabilidad" in r.json()["result"]["scenario_note"]


def test_otros_modos(client):
    assert (
        cagr(client, {"mode": "real", "nominal": "0.10", "inflation": "0.04"}).json()["result"]["real_return"]
        == "0.057692"
    )
    fx = cagr(client, {"mode": "fx", "local_return": "0.10", "fx_start": 18, "fx_end": "19.8"}).json()[
        "result"
    ]
    assert fx["return_in_mxn"] == "0.210000" and fx["fx_component"] == "0.100000"
    tw = cagr(client, {"mode": "twr", "subperiods": [{"begin_value": 100, "end_value": 110},
                                                      {"begin_value": 110, "end_value": 231, "flow": 100}]})  # fmt: skip
    assert tw.json()["result"]["twr"] == "0.210000"
    dv = cagr(client, {"mode": "dividends", "shares": 10, "start_price": 100, "end_price": 100, "withholding": "0.3",
                       "dividends": [{"per_share": 10, "reinvest_price": 100}]})  # fmt: skip
    assert dv.json()["result"]["total_return_reinvested_net"] == "0.070000"


def test_precio_maximo(client):
    r = client.post("/api/v1/calculators/target-buy-price",
                    {"future_value": "161.051", "years": 5, "target_return": "0.10",
                     "estimated_value": 120, "margin_of_safety": "0.25"}, format="json")  # fmt: skip
    assert r.status_code == 200, r.content
    res = r.json()["result"]
    assert res["max_price_by_return"] == "100.000000" and res["max_price_by_margin_of_safety"] == "90.000000"
    assert res["conservative_max_price"] == "90.000000"


@pytest.mark.parametrize(
    "body,msg",
    [
        ({}, "mode"), ({"mode": "otro"}, "mode"),
        ({"mode": "historical", "initial": "abc", "final": 1, "years": 1}, "inválido"),
        ({"mode": "historical", "initial": 0, "final": 1, "years": 1}, "mayores que cero"),
        ({"mode": "historical", "initial": 1, "final": "NaN", "years": 1}, "inválido"),
        ({"mode": "historical", "initial": 1, "final": 2, "years": 1000}, "no exceder"),
        ({"mode": "historical", "initial": 1, "final": 2, "start_date": "ayer", "end_date": "2026-01-01"}, "fecha"),
        ({"mode": "twr", "subperiods": []}, "lista"),
        ({"mode": "twr", "subperiods": "no"}, "lista"),
        ({"mode": "projected", "buy_price": 1, "fundamental_initial": 1, "growth": 0, "years": 3, "exit_multiple": 1,
          "dividends_per_share": [1]}, "un dividendo por año"),
        ({"mode": "projected", "buy_price": 1, "fundamental_initial": 1, "growth": 0, "years": 3, "exit_multiple": 1,
          "scenarios": {"bear": "no"}}, "objeto"),
        ({"mode": "projected", "buy_price": 1, "fundamental_initial": 1, "growth": 0, "years": 3, "exit_multiple": 1,
          "dividends_per_share": [1] * 100}, "lista"),
    ],
)  # fmt: skip
def test_entradas_invalidas_dan_400(client, body, msg):
    r = cagr(client, body)
    assert r.status_code == 400 and msg in str(r.json()), r.content


def test_error_de_formato_no_filtra_detalles(client):
    r = cagr(client, {"mode": "twr", "subperiods": [5]})
    assert r.status_code == 400 or r.status_code == 200
    r = cagr(client, {"mode": "historical", "initial": [1], "final": 2, "years": 1})
    assert r.status_code == 400 and "Traceback" not in str(r.json())


def test_exportacion_csv_de_la_hipotesis(client):
    r = cagr(client, {"mode": "historical", "initial": 100, "final": 121, "years": 2}, "?format=csv")
    assert r.status_code == 200 and r["Content-Type"].startswith("text/csv")
    assert "hipotesis-historical.csv" in r["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(r.content.decode())))
    assert rows[0] == ["seccion", "campo", "valor"]
    assert ["entradas", "initial", "100"] in rows and ["resultados", "cagr", "0.100000"] in rows
    assert (
        cagr(client, {"mode": "historical", "initial": 1, "final": 2, "years": 1}, "?format=xml").status_code
        == 400
    )


def test_csv_neutraliza_formulas(client):
    # Un texto de entrada que empiece por '=' no debe quedar como fórmula de hoja de cálculo.
    from .service import to_csv

    text = to_csv({"mode": "x", "inputs": {"nota": '=HYPERLINK("http://x")', "n": "-5"}, "result": {}})
    assert "'=HYPERLINK" in text and ",-5" in text


def test_requiere_autenticacion(db):
    for url in ("cagr", "target-buy-price"):
        assert APIClient().post(f"/api/v1/calculators/{url}", {}, format="json").status_code in (401, 403)
