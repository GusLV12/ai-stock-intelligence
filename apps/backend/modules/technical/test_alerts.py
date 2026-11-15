from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.pricing.history_models import DailyClose
from modules.pricing.services import record_price

from .models import PriceAlert


@pytest.fixture
def ctx(db):
    user = get_user_model().objects.create_user("ana", password="x")
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=8)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    c = APIClient()
    c.force_authenticate(user)
    return c, user, ins


def post(c, body):
    return c.post("/api/v1/technical/alerts", body, format="json")


def test_alerta_de_precio_se_dispara_una_sola_vez_con_el_detalle(ctx):
    c, _, ins = ctx
    record_price(ins, D(90), "MANUAL", datetime.now(UTC) - timedelta(hours=3))
    a = post(c, {"ticker": "aaa", "kind": "PRICE_ABOVE", "threshold": "100", "note": "romper 100"}).json()
    assert a["active"] is True and a["threshold"] == "100.000000"
    assert c.post("/api/v1/technical/alerts/evaluate").json()["triggered"] == []  # 90 < 100: aún no
    record_price(ins, D(101), "YAHOO_UNOFFICIAL", datetime.now(UTC) - timedelta(hours=2))
    r = c.post("/api/v1/technical/alerts/evaluate").json()
    d = r["triggered"][0]
    assert d["id"] == a["id"] and d["active"] is False and d["triggered_at"]
    assert d["triggered_detail"]["price"] == "101" and d["triggered_detail"]["provider"] == "YAHOO_UNOFFICIAL"
    assert d["triggered_detail"]["age_hours"] == 2.0 and d["triggered_detail"]["stale"] is False
    assert "no se envía ninguna notificación" in r["note"]
    assert c.post("/api/v1/technical/alerts/evaluate").json()["triggered"] == []  # idempotente


def test_precio_bajo_y_dato_viejo_se_marca_como_stale(ctx):
    c, _, ins = ctx
    record_price(ins, D(50), "MANUAL", datetime.now(UTC) - timedelta(days=10))
    post(c, {"ticker": "AAA", "kind": "PRICE_BELOW", "threshold": "60"})
    d = c.post("/api/v1/technical/alerts/evaluate").json()["triggered"][0]["triggered_detail"]
    assert d["stale"] is True and d["age_hours"] > 72  # se dispara, pero declara que el precio es viejo


def test_alerta_de_senal_usa_las_velas_guardadas_y_su_regla(ctx):
    c, _, ins = ctx
    today = datetime.now(UTC).date()
    rows = [
        DailyClose(
            instrument=ins,
            date=today - timedelta(days=30 - i),
            close=D(100),
            currency="USD",
            provider="YAHOO_UNOFFICIAL",
        )
        for i in range(29)
    ]
    rows.append(
        DailyClose(instrument=ins, date=today, close=D(108), currency="USD", provider="YAHOO_UNOFFICIAL")
    )
    DailyClose.objects.bulk_create(rows)
    post(c, {"ticker": "AAA", "kind": "SIGNAL", "signal_id": "breakout"})
    post(c, {"ticker": "AAA", "kind": "SIGNAL", "signal_id": "oversold"})
    fired = c.post("/api/v1/technical/alerts/evaluate").json()["triggered"]
    assert [f["signal_id"] for f in fired] == ["breakout"]
    assert "máximo de los 20" in fired[0]["triggered_detail"]["rule"] and fired[0]["triggered_detail"][
        "as_of"
    ] == str(today)
    pendiente = [a for a in c.get("/api/v1/technical/alerts").json() if a["active"]]
    assert [a["signal_id"] for a in pendiente] == ["oversold"]


def test_sin_datos_no_se_dispara_ni_falla(ctx):
    c, _, _ = ctx
    post(c, {"ticker": "AAA", "kind": "PRICE_ABOVE", "threshold": "1"})
    post(c, {"ticker": "AAA", "kind": "SIGNAL", "signal_id": "breakout"})
    r = c.post("/api/v1/technical/alerts/evaluate").json()
    assert r["triggered"] == [] and r["evaluated"] == 2


def test_cancelar_una_alerta(ctx):
    c, _, ins = ctx
    record_price(ins, D(200), "MANUAL", datetime.now(UTC))
    a = post(c, {"ticker": "AAA", "kind": "PRICE_ABOVE", "threshold": "100"}).json()
    assert c.post(f"/api/v1/technical/alerts/{a['id']}/cancel").json()["active"] is False
    assert c.post("/api/v1/technical/alerts/evaluate").json()["triggered"] == []  # cancelada: no se dispara


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"ticker": "ZZZ", "kind": "PRICE_ABOVE", "threshold": 1},
        {"ticker": "AAA", "kind": "otro"},
        {"ticker": "AAA", "kind": "PRICE_ABOVE"},
        {"ticker": "AAA", "kind": "PRICE_ABOVE", "threshold": "abc"},
        {"ticker": "AAA", "kind": "PRICE_ABOVE", "threshold": 0},
        {"ticker": "AAA", "kind": "PRICE_BELOW", "threshold": "NaN"},
        {"ticker": "AAA", "kind": "SIGNAL"},
        {"ticker": "AAA", "kind": "SIGNAL", "signal_id": "inventada"},
    ],
)
def test_validaciones(ctx, body):
    assert post(ctx[0], body).status_code == 400


def test_limite_de_alertas_activas(ctx):
    c, user, ins = ctx
    PriceAlert.objects.bulk_create(
        [PriceAlert(user=user, instrument=ins, kind="PRICE_ABOVE", threshold=D(1)) for _ in range(100)]
    )
    assert post(c, {"ticker": "AAA", "kind": "PRICE_ABOVE", "threshold": 1}).status_code == 400


def test_aislamiento_y_autenticacion(ctx):
    c, _, ins = ctx
    record_price(ins, D(200), "MANUAL", datetime.now(UTC))
    a = post(c, {"ticker": "AAA", "kind": "PRICE_ABOVE", "threshold": "100"}).json()
    otro = APIClient()
    otro.force_authenticate(get_user_model().objects.create_user("beto", password="x"))
    assert otro.get("/api/v1/technical/alerts").json() == []
    assert otro.post(f"/api/v1/technical/alerts/{a['id']}/cancel").status_code == 404
    assert (
        otro.post("/api/v1/technical/alerts/evaluate").json()["triggered"] == []
    )  # no evalúa alertas ajenas
    assert PriceAlert.objects.get(pk=a["id"]).active is True
    assert APIClient().get("/api/v1/technical/alerts").status_code in (401, 403)


def test_comando_evalua_todas_las_alertas(ctx):
    c, _, ins = ctx
    record_price(ins, D(200), "MANUAL", datetime.now(UTC))
    post(c, {"ticker": "AAA", "kind": "PRICE_ABOVE", "threshold": "100"})
    out = StringIO()
    call_command("evaluate_alerts", stdout=out)
    assert "evaluadas: 1" in out.getvalue() and "disparadas: 1" in out.getvalue()
    out = StringIO()
    call_command("evaluate_alerts", stdout=out)
    assert "evaluadas: 0" in out.getvalue()
