from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from rest_framework.test import APIClient

from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.pricing.services import record_price
from modules.valuations.models import Valuation

from .thesis_models import InvestmentThesis, ThesisFollowUp

BODY = {"title": "AAA por márgenes", "thesis": "Los márgenes seguirán subiendo.", "risks": "Competencia y deuda.",
        "invalidation": "Margen bruto bajo 30% dos trimestres seguidos.", "horizon": "3 años"}  # fmt: skip


@pytest.fixture
def ctx(db):
    user = get_user_model().objects.create_user("ana", password="x")
    c = APIClient()
    c.force_authenticate(user)
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=8)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    record_price(ins, D(100), "MANUAL", datetime(2026, 11, 13, tzinfo=UTC))
    return c, user, ins


def test_crea_tesis_con_precio_y_procedencia_del_momento(ctx):
    c, _, ins = ctx
    r = c.post(
        "/api/v1/portfolio/theses", {**BODY, "instrument": ins.id, "sources": ["10-K 2025"]}, format="json"
    )
    assert r.status_code == 201, r.content
    d = r.json()
    assert d["price"] == "100.000000" and d["price_source"]["provider"] == "MANUAL" and d["status"] == "OPEN"
    got = c.get(f"/api/v1/portfolio/theses/{d['id']}").json()
    assert (
        got["invalidation"].startswith("Margen bruto")
        and got["sources"] == ["10-K 2025"]
        and got["follow_ups"] == []
    )


def test_seguimiento_registra_el_precio_de_ese_momento_y_el_cambio(ctx):
    c, _, ins = ctx
    tid = c.post("/api/v1/portfolio/theses", {**BODY, "instrument": ins.id}, format="json").json()["id"]
    record_price(ins, D(125), "MANUAL", datetime(2026, 11, 14, tzinfo=UTC))
    r = c.post(
        f"/api/v1/portfolio/theses/{tid}/follow-ups",
        {"status": "CONFIRMED", "note": "Resultados mejores."},
        format="json",
    )
    assert r.status_code == 201, r.content
    assert r.json()["price"] == "125.000000" and r.json()["change_since_thesis"] == "0.250000"
    d = c.get(f"/api/v1/portfolio/theses/{tid}").json()
    assert d["status"] == "CONFIRMED" and d["follow_ups"][0]["note"] == "Resultados mejores."
    assert c.get("/api/v1/portfolio/theses").json()[0]["follow_ups"] == 1
    # el precio original de la tesis no cambia aunque el precio actual sí
    assert d["price"] == "100.000000"


def test_el_estado_es_el_del_ultimo_seguimiento_y_se_conserva_el_historial(ctx):
    c, _, ins = ctx
    tid = c.post("/api/v1/portfolio/theses", {**BODY, "instrument": ins.id}, format="json").json()["id"]
    for st in ("CONFIRMED", "INVALIDATED"):
        c.post(
            f"/api/v1/portfolio/theses/{tid}/follow-ups",
            {"status": st, "note": f"estado {st}"},
            format="json",
        )
    d = c.get(f"/api/v1/portfolio/theses/{tid}").json()
    assert d["status"] == "INVALIDATED" and [f["status"] for f in d["follow_ups"]] == [
        "CONFIRMED",
        "INVALIDATED",
    ]


def test_tesis_y_seguimientos_son_inmutables(ctx):
    c, _, ins = ctx
    tid = c.post("/api/v1/portfolio/theses", {**BODY, "instrument": ins.id}, format="json").json()["id"]
    c.post(f"/api/v1/portfolio/theses/{tid}/follow-ups", {"note": "n"}, format="json")
    with pytest.raises(ProtectedError):
        InvestmentThesis.objects.get().delete()
    with pytest.raises(ProtectedError):
        ThesisFollowUp.objects.get().delete()
    # no existe ruta para editar o borrar
    assert c.put(f"/api/v1/portfolio/theses/{tid}", {}, format="json").status_code == 405
    assert c.delete(f"/api/v1/portfolio/theses/{tid}").status_code == 405


def test_sin_instrumento_se_guarda_sin_precio(ctx):
    c, _, _ = ctx
    d = c.post("/api/v1/portfolio/theses", BODY, format="json").json()
    assert d["price"] is None and d["price_source"] == {}


def test_valoracion_vinculada_debe_ser_propia(ctx):
    c, user, _ = ctx
    mine = Valuation.objects.create(user=user, model_version="valuation-v1", inputs={}, output={})
    other_user = get_user_model().objects.create_user("beto", password="x")
    theirs = Valuation.objects.create(user=other_user, model_version="valuation-v1", inputs={}, output={})
    assert (
        c.post("/api/v1/portfolio/theses", {**BODY, "valuation_id": mine.id}, format="json").status_code
        == 201
    )
    assert (
        c.post("/api/v1/portfolio/theses", {**BODY, "valuation_id": theirs.id}, format="json").status_code
        == 400
    )


@pytest.mark.parametrize(
    "over",
    [
        {"title": ""},
        {"thesis": "   "},
        {"risks": None},
        {"invalidation": ""},
        {"title": "x" * 151},
        {"thesis": "x" * 5001},
        {"strategy": "otra"},
        {"instrument": 99999},
        {"sources": "no"},
        {"sources": ["x"] * 21},
        {"sources": [5]},
        {"thesis": 5},
    ],
)
def test_validaciones(ctx, over):
    c, _, _ = ctx
    body = {**BODY, **over}
    for k in [k for k, v in body.items() if v is None]:
        body.pop(k)
    assert c.post("/api/v1/portfolio/theses", body, format="json").status_code == 400


def test_seguimiento_invalido_y_aislamiento(ctx):
    c, _, _ = ctx
    tid = c.post("/api/v1/portfolio/theses", BODY, format="json").json()["id"]
    assert (
        c.post(
            f"/api/v1/portfolio/theses/{tid}/follow-ups", {"status": "RARO", "note": "x"}, format="json"
        ).status_code
        == 400
    )
    assert (
        c.post(f"/api/v1/portfolio/theses/{tid}/follow-ups", {"note": ""}, format="json").status_code == 400
    )
    otro = APIClient()
    otro.force_authenticate(get_user_model().objects.create_user("beto", password="x"))
    assert otro.get(f"/api/v1/portfolio/theses/{tid}").status_code == 404
    assert (
        otro.post(f"/api/v1/portfolio/theses/{tid}/follow-ups", {"note": "x"}, format="json").status_code
        == 404
    )
    assert otro.get("/api/v1/portfolio/theses").json() == []
    assert APIClient().get("/api/v1/portfolio/theses").status_code in (401, 403)
