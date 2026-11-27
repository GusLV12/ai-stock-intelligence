import csv
import io
from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from openpyxl import load_workbook
from rest_framework.test import APIClient

from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.pricing.services import record_price
from modules.valuations.models import Valuation

from .screen_models import ScreenRun
from .screener import ScreenError, run_screen
from .services import import_financials


def make(ticker, cls="STOCK", sector="non_financial"):
    ins = Instrument.objects.create(asset_class=cls, name=ticker, quantity_scale=8, sector_kind=sector)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=ticker)
    return ins


def years(ticker, good=True, base=2024, **extra25):
    """Dos años consecutivos. `good`: mejora en los 9 criterios; si no, empeora."""
    y24 = {"revenue": "1000", "gross_profit": "300", "net_income": "50", "cfo": "60", "capex": "20", "total_assets": "1000",
           "current_assets": "400", "current_liabilities": "300", "long_term_debt": "300", "total_debt": "350", "shares_outstanding": "100"}  # fmt: skip
    if good:
        y25 = {"revenue": "1200", "gross_profit": "400", "net_income": "100", "cfo": "150", "capex": "30", "total_assets": "1100",
               "current_assets": "500", "current_liabilities": "300", "long_term_debt": "250", "total_debt": "300", "shares_outstanding": "100"}  # fmt: skip
    else:
        y25 = {"revenue": "900", "gross_profit": "200", "net_income": "-10", "cfo": "-5", "capex": "30", "total_assets": "1000",
               "current_assets": "300", "current_liabilities": "400", "long_term_debt": "500", "total_debt": "700", "shares_outstanding": "130"}  # fmt: skip
    y25.update(extra25)
    return [
        {
            "ticker": ticker,
            "fiscal_year": base,
            "period_end": f"{base}-12-31",
            "published_at": f"{base + 1}-02-20",
            **y24,
        },
        {
            "ticker": ticker,
            "fiscal_year": base + 1,
            "period_end": f"{base + 1}-12-31",
            "published_at": f"{base + 2}-02-20",
            **y25,
        },
    ]


def value(user, ins, central, when=None):
    return Valuation.objects.create(
        user=user, instrument=ins, model_version="valuation-v1", inputs={}, output={"central_value": central}
    )


@pytest.fixture
def world(db):
    user = get_user_model().objects.create_user("ana", password="x")
    a, b, bank, nodata, etf = (
        make("AAA"),
        make("BBB"),
        make("BNK", sector="bank"),
        make("NOD"),
        make("ETF1", cls="ETF"),
    )
    import_financials(years("AAA", True) + years("BBB", False) + years("BNK", True))
    now = datetime.now(UTC)
    for ins, price in ((a, D(70)), (b, D(50)), (bank, D(10))):
        record_price(ins, price, "MANUAL", now)
    value(user, a, "100")  # margen (100-70)/100 = 30%
    value(user, b, "60")
    return user, {"a": a, "b": b, "bank": bank, "nodata": nodata, "etf": etf}


def by_ticker(res):
    return {r["ticker"]: r for r in res["rows"]}


def test_pasa_la_empresa_buena_y_se_explica_por_que_falla_la_mala(world):
    user, _ = world
    res = run_screen(
        user,
        {
            "min_fscore": 7,
            "min_margin_of_safety": "0.2",
            "require_positive_fcf": True,
            "max_debt_to_assets": "0.5",
        },
    )
    rows = by_ticker(res)
    assert rows["AAA"]["passed"] is True and res["candidates"] == ["AAA"]
    assert rows["BBB"]["passed"] is False
    assert {"min_fscore", "require_positive_fcf", "max_debt_to_assets"} <= set(rows["BBB"]["failed_filters"])
    assert all("detail" in c for c in rows["BBB"]["checks"])  # cada filtro explica su resultado


def test_dato_faltante_excluye_con_motivo_no_aprueba_ni_cuenta_cero(world):
    user, _ = world
    res = run_screen(user, {"min_fscore": 7})
    bank = by_ticker(res)["BNK"]
    assert bank["passed"] is False and bank["missing_data_filters"] == ["min_fscore"]
    assert (
        bank["fscore"]["status"] == "no_aplica"
        and bank["ratings"]["financial_quality"]["label"] == "No aplica"
    )
    # sin valoración guardada tampoco aprueba el filtro de margen
    res = run_screen(user, {"min_margin_of_safety": "0.1"})
    assert by_ticker(res)["BNK"]["missing_data_filters"] == ["min_margin_of_safety"]


def test_fscore_parcial_se_excluye_salvo_que_se_permita(world):
    user, _ = world
    partial = make("PAR")
    rows = years("PAR", True)
    for r in rows:
        r.pop("long_term_debt")  # sin deuda: 1 criterio no evaluable
    import_financials(rows)
    strict = by_ticker(run_screen(user, {"min_fscore": 7}))["PAR"]
    assert strict["passed"] is False and "parcial" in strict["checks"][0]["detail"]
    loose = by_ticker(run_screen(user, {"min_fscore": 7, "allow_partial_fscore": True}))["PAR"]
    assert loose["passed"] is True and loose["fscore"]["evaluable_criteria"] == 8 and partial.id


def test_universo_cobertura_y_solo_acciones(world):
    user, _ = world
    res = run_screen(user, {})
    assert res["coverage"]["universe"] == 4  # AAA, BBB, BNK, NOD (el ETF no se evalúa)
    assert res["coverage"]["with_financials"] == 3 and res["coverage"]["without_financials"] == ["NOD"]
    assert "ETF1" not in by_ticker(res)
    assert res["consensus"]["enabled"] is False and "licencia" in res["consensus"]["reason"]
    assert "no es una recomendación" in res["disclaimer"]


def test_ratings_por_dimension_sin_puntaje_agregado(world):
    user, _ = world
    r = by_ticker(run_screen(user, {}))
    a, b = r["AAA"]["ratings"], r["BBB"]["ratings"]
    assert (
        a["valuation"]["label"] == "Atractiva"
        and a["financial_quality"]["label"] == "Alta"
        and a["growth"]["label"] == "Alto"
    )
    assert a["risk"]["label"] == "Bajo" and a["data_quality"]["label"] == "Buena"
    assert b["valuation"]["label"] == "Neutral" and b["financial_quality"]["label"] == "Baja"
    assert b["growth"]["label"] == "Negativo" and b["risk"]["label"] == "Alto"
    assert "score" not in a and "total" not in a  # el plan: sin agregado hasta validarlo
    assert all("basis" in v for v in a.values())


def test_margen_se_recalcula_con_el_precio_actual(world):
    user, w = world
    assert by_ticker(run_screen(user, {}))["AAA"]["metrics"]["margin_of_safety"] == "0.300000"
    record_price(w["a"], D(95), "MANUAL", datetime.now(UTC))  # el precio sube: el margen baja
    assert by_ticker(run_screen(user, {}))["AAA"]["metrics"]["margin_of_safety"] == "0.050000"


def test_as_of_no_ve_datos_aun_no_publicados(world):
    user, _ = world
    res = run_screen(user, {"as_of": "2025-06-01"})  # solo el 2024 (publicado 2025-02-20) era público
    aaa = by_ticker(res)["AAA"]
    assert aaa["data_refs"]["fiscal_year"] == 2024 and aaa["fscore"]["status"] == "parcial"
    assert run_screen(user, {"as_of": "2024-01-01"})["coverage"]["with_financials"] == 0


def test_reproducible_mismos_filtros_mismos_resultados(world):
    user, _ = world
    p = {"min_fscore": 7, "as_of": "2026-06-01"}
    assert run_screen(user, p) == run_screen(user, p)


def test_filtro_de_vigencia_de_datos(world):
    user, _ = world
    stale = make("OLD")
    import_financials(years("OLD", True, base=2018))
    r = by_ticker(run_screen(user, {"require_fresh_data": True}))
    assert r["OLD"]["failed_filters"] == ["require_fresh_data"] and r["AAA"]["passed"] is True and stale.id
    assert r["OLD"]["ratings"]["data_quality"]["label"] != "Buena"


def test_candidatas_ordenadas_por_margen(world):
    user, _ = world
    c = make("CCC")
    import_financials(years("CCC", True))
    record_price(c, D(40), "MANUAL", datetime.now(UTC))
    value(user, c, "100")  # margen 60%
    assert run_screen(user, {"min_fscore": 7})["candidates"] == ["CCC", "AAA"]


@pytest.mark.parametrize(
    "params,msg",
    [({"min_fscore": 10}, "0 a 9"), ({"min_fscore": "x"}, "0 a 9"), ({"min_margin_of_safety": "abc"}, "inválido"),
     ({"min_margin_of_safety": "5"}, "fuera de rango"), ({"as_of": "ayer"}, "fecha ISO"), ("no-objeto", "objeto"),
     ({"max_debt_to_assets": "NaN"}, "fuera de rango")],
)  # fmt: skip
def test_filtros_invalidos(world, params, msg):
    with pytest.raises(ScreenError, match=msg):
        run_screen(world[0], params)


# ---------- API ----------


def api_client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_api_guarda_la_corrida_y_exporta_csv(world):
    user, _ = world
    c = api_client(user)
    r = c.post("/api/v1/screeners/fair-value", {"min_fscore": 7}, format="json")
    assert r.status_code == 201, r.content
    run_id = r.json()["id"]
    assert r.json()["candidates"] == ["AAA"] and ScreenRun.objects.get().model_version == "screener-v1"
    listing = c.get("/api/v1/screeners/runs").json()
    assert listing[0]["id"] == run_id and listing[0]["coverage"]["with_financials"] == 3
    detail = c.get(f"/api/v1/screeners/runs/{run_id}").json()
    assert detail["rows"][0]["data_refs"]["fiscal_year"] == 2025
    resp = c.get(f"/api/v1/screeners/runs/{run_id}?format=csv")
    assert resp.status_code == 200 and resp["Content-Type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(resp.content.decode())))
    assert rows[0][0] == "ticker" and any(r[0] == "AAA" and r[1] == "si" for r in rows[1:])
    excel = c.get(f"/api/v1/screeners/runs/{run_id}?format=xlsx")
    assert excel.status_code == 200
    workbook = load_workbook(io.BytesIO(excel.content), data_only=False)
    assert next(iter(workbook.active.values))[0] == "ticker"
    assert any(row[0] == "AAA" and row[1] == "si" for row in list(workbook.active.values)[1:])
    assert workbook.active.cell(2, 5).data_type == "n"
    workbook.close()
    assert c.get(f"/api/v1/screeners/runs/{run_id}?format=xml").status_code == 400


def test_api_valida_y_aisla_usuarios(world):
    user, _ = world
    c = api_client(user)
    assert c.post("/api/v1/screeners/fair-value", {"min_fscore": 99}, format="json").status_code == 400
    run_id = c.post("/api/v1/screeners/fair-value", {}, format="json").json()["id"]
    otro = api_client(get_user_model().objects.create_user("beto", password="x"))
    assert otro.get(f"/api/v1/screeners/runs/{run_id}").status_code == 404
    assert otro.get(f"/api/v1/screeners/runs/{run_id}?format=xlsx").status_code == 404
    assert otro.get("/api/v1/screeners/runs").json() == []
    # el otro usuario no tiene valoraciones: su margen es "sin dato"
    mine = otro.post("/api/v1/screeners/fair-value", {"min_margin_of_safety": "0.1"}, format="json").json()
    assert mine["candidates"] == []
    assert APIClient().post("/api/v1/screeners/fair-value", {}, format="json").status_code in (401, 403)


def test_las_corridas_son_inmutables(world):
    user, _ = world
    api_client(user).post("/api/v1/screeners/fair-value", {}, format="json")
    with pytest.raises(ProtectedError):
        ScreenRun.objects.get().delete()


def test_csv_neutraliza_formulas():
    from .screener_api import _safe

    assert (
        _safe("=SUM(A1)") == "'=SUM(A1)"
        and _safe("AAA") == "AAA"
        and _safe("-5") == "-5"
        and _safe("-x") == "'-x"
    )
