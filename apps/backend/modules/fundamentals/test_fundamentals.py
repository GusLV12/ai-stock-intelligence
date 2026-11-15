from datetime import date
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import ProtectedError
from rest_framework.test import APIClient

from modules.instruments.models import Instrument, InstrumentIdentifier

from .models import FinancialYear
from .services import FundamentalsError, import_financials, latest_years


@pytest.fixture
def aaa(db):
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=8)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    return ins


def row(**over):
    base = {"ticker": "AAA", "fiscal_year": 2025, "period_end": "2025-12-31", "published_at": "2026-02-20",
            "revenue": "1000", "gross_profit": "400", "net_income": "100", "cfo": "150", "total_assets": "2000"}  # fmt: skip
    return {**base, **over}


def test_importa_y_es_idempotente(aaa):
    assert import_financials([row()]) == {"created": 1, "new_versions": 0, "unchanged": 0}
    assert import_financials([row()]) == {"created": 0, "new_versions": 0, "unchanged": 1}
    assert FinancialYear.objects.count() == 1


def test_una_reexpresion_crea_version_nueva_y_conserva_la_anterior(aaa):
    import_financials([row()])
    res = import_financials([row(net_income="90", published_at="2026-08-01")])
    assert res["new_versions"] == 1
    assert sorted(FinancialYear.objects.values_list("version", "net_income")) == [(1, D(100)), (2, D(90))]
    # Con fecha "as_of" anterior a la reexpresión solo se conocía la versión 1 (sin sesgo de anticipación).
    assert latest_years(aaa, as_of=date(2026, 6, 1))[0].net_income == D(100)
    assert latest_years(aaa, as_of=date(2026, 9, 1))[0].net_income == D(90)
    assert latest_years(aaa)[0].version == 2
    assert latest_years(aaa, as_of=date(2026, 1, 1)) == []  # aún no publicado


def test_sin_fecha_de_publicacion_no_se_puede_probar_que_se_conocia(aaa):
    import_financials([row(published_at="")])
    assert latest_years(aaa, as_of=date(2030, 1, 1)) == [] and len(latest_years(aaa)) == 1


def test_todo_o_nada_y_errores_por_fila(aaa):
    rows = [row(), row(ticker="ZZZ"), row(fiscal_year="abc"), row(period_end="ayer"), row(cash="-5"), row(total_assets="0"),
            row(net_income="x"), row(fiscal_year=1900), row(published_at="2025-01-01"), row(current_assets="9999"),
            row(gross_profit="5000"), {"ticker": "AAA", "fiscal_year": 2024, "period_end": "2024-12-31"}, "no-objeto"]  # fmt: skip
    with pytest.raises(FundamentalsError) as exc:
        import_financials(rows)
    msgs = " | ".join(e["error"] for e in exc.value.errors)
    for frag in ("ticker desconocido", "entero requerido", "fecha ISO", "no puede ser negativo", "número inválido",
                 "mayor que cero", "fuera de rango", "anterior a period_end", "current_assets", "gross_profit", "ningún dato", "objeto"):
        assert frag in msgs, frag
    assert FinancialYear.objects.count() == 0  # la fila 1 era válida pero no se guardó


def test_anio_repetido_en_el_mismo_archivo_se_rechaza(aaa):
    with pytest.raises(FundamentalsError) as exc:
        import_financials([row(), row(cfo="1")])
    assert "repetido" in exc.value.errors[0]["error"]


def test_datos_ausentes_quedan_nulos_no_cero(aaa):
    import_financials([{"ticker": "AAA", "fiscal_year": 2024, "period_end": "2024-12-31", "revenue": "10"}])
    fy = FinancialYear.objects.get()
    assert fy.net_income is None and fy.free_cash_flow is None
    import_financials([row(cfo="150", capex="50")])
    assert FinancialYear.objects.get(fiscal_year=2025).free_cash_flow == D(100)


def test_los_datos_financieros_no_se_borran(aaa):
    import_financials([row()])
    with pytest.raises(ProtectedError):
        FinancialYear.objects.get().delete()


@pytest.fixture
def client(db):
    c = APIClient()
    c.force_authenticate(get_user_model().objects.create_user("ana", password="x"))
    return c


def test_api_importa_json_csv_y_consulta(client, aaa):
    r = client.post("/api/v1/fundamentals/import", {"rows": [row()]}, format="json")
    assert r.status_code == 201 and r.json()["created"] == 1
    csv_text = "ticker,fiscal_year,period_end,published_at,revenue,net_income\nAAA,2024,2024-12-31,2025-02-20,900,80\n"
    f = SimpleUploadedFile("fin.csv", csv_text.encode(), content_type="text/csv")
    r = client.post("/api/v1/fundamentals/import", {"file": f}, format="multipart")
    assert r.status_code == 201 and r.json()["created"] == 1
    got = client.get(f"/api/v1/fundamentals/{aaa.id}").json()
    assert [g["fiscal_year"] for g in got] == [2025, 2024] and got[1]["source"] == "CSV"
    assert got[0]["total_assets"] == "2000.000000" and got[1]["cfo"] is None


def test_api_errores_y_autenticacion(client, aaa):
    r = client.post("/api/v1/fundamentals/import", {"rows": [row(ticker="ZZZ")]}, format="json")
    assert r.status_code == 400 and str(r.json()["errors"][0]["row"]) == "1"
    assert client.post("/api/v1/fundamentals/import", {}, format="json").status_code == 400
    bad = SimpleUploadedFile("x.csv", b"col1,col2\n1,2\n", content_type="text/csv")
    assert client.post("/api/v1/fundamentals/import", {"file": bad}, format="multipart").status_code == 400
    assert client.get("/api/v1/fundamentals/99999").status_code == 404
    assert APIClient().post("/api/v1/fundamentals/import", {}, format="json").status_code in (401, 403)
