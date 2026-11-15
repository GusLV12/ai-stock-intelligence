from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy
from modules.pricing.services import record_price

from . import api as assistant_api
from .models import AssistantLog
from .ollama import OllamaClient
from .test_agent import FakeClient, call, say


@pytest.fixture
def client(db):
    user = get_user_model().objects.create_user("ana", password="x")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=8)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    record_buy(
        account=acc, instrument=ins, quantity=D(2), price=D(100), executed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    record_price(ins, D(150), "MANUAL", datetime(2026, 11, 13, tzinfo=UTC))
    c = APIClient()
    c.force_authenticate(user)
    return c


def use_model(monkeypatch, fake):
    fake.model = "qwen3:1.7b"
    monkeypatch.setattr(assistant_api, "get_client", lambda: fake)


def test_chat_ok_con_fuentes_y_registro(client, monkeypatch):
    use_model(monkeypatch, FakeClient(call("list_positions"), say("AAA vale $300.00 (+50.00).")))
    r = client.post("/api/v1/assistant/chat", {"message": "¿Cuánto vale AAA?"}, format="json")
    assert r.status_code == 200, r.content
    d = r.json()
    assert (
        d["answer"].startswith("AAA vale")
        and d["unverified_numbers"] == []
        and d["verification_note"] is None
    )
    assert d["tool_calls"][0]["name"] == "list_positions" and d["sources"][0]["providers"] == ["MANUAL"]
    log = AssistantLog.objects.get()
    assert log.status == "ok" and log.tools == ["list_positions"] and log.model == "qwen3:1.7b"
    assert log.question == "¿Cuánto vale AAA?"


def test_chat_marca_cifras_inventadas(client, monkeypatch):
    use_model(monkeypatch, FakeClient(call("list_positions"), say("AAA vale $999.00.")))
    d = client.post("/api/v1/assistant/chat", {"message": "x"}, format="json").json()
    assert d["unverified_numbers"] == ["$999.00"] and "no las tomes" in d["verification_note"]
    assert AssistantLog.objects.get().unverified_count == 1


def test_ollama_apagado_da_503_y_el_resto_de_la_app_sigue(client, monkeypatch):
    monkeypatch.setattr(
        assistant_api, "get_client", lambda: OllamaClient(base_url="http://127.0.0.1:9", timeout=2)
    )
    r = client.post("/api/v1/assistant/chat", {"message": "hola"}, format="json")
    assert r.status_code == 503 and "No se pudo conectar" in r.json()["detail"]
    assert AssistantLog.objects.get().status == "unavailable"
    # el cálculo determinista no depende del modelo
    assert (
        client.get("/api/v1/portfolio/dashboard").json()["totals_by_currency"]["USD"]["market_value"]
        == "300.00"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": ""},
        {"message": "   "},
        {"message": 5},
        {"message": "x" * 2001},
        {"message": "x", "history": "no"},
        {"message": "x", "history": [{}] * 13},
    ],
)
def test_validaciones_de_entrada(client, monkeypatch, payload):
    use_model(monkeypatch, FakeClient(say("no debe llegar")))
    assert client.post("/api/v1/assistant/chat", payload, format="json").status_code == 400


def test_requiere_autenticacion(db):
    assert APIClient().post("/api/v1/assistant/chat", {"message": "x"}, format="json").status_code in (
        401,
        403,
    )


def test_limite_de_uso_por_usuario(client, monkeypatch):
    use_model(monkeypatch, FakeClient(*[say("ok")] * 12))
    codes = [
        client.post("/api/v1/assistant/chat", {"message": "x"}, format="json").status_code for _ in range(11)
    ]
    assert codes[:10] == [200] * 10 and codes[10] == 429
