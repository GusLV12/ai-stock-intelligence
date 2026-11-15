import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from io import StringIO
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import get_resolver
from rest_framework.test import APIClient

from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.pricing.services import record_price
from modules.quant.models import QuantExperiment

from .models import PaperAccount, PaperOrder

BASE = "/api/v1/paper"


@pytest.fixture
def ctx(db):
    user = get_user_model().objects.create_user("ana", password="x")
    c = APIClient()
    c.force_authenticate(user)
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=2)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    record_price(ins, D(100), "MANUAL", datetime.now(UTC) - timedelta(hours=1))
    return c, user, ins


def new_account(c, **kw):
    body = {
        "name": "Práctica",
        "initial_cash": "10000",
        "config": {"slippage_bps": 10, "max_position_pct": 0.5},
        **kw,
    }
    return c.post(f"{BASE}/accounts", body, format="json")


def order(c, acc, **kw):
    return c.post(
        f"{BASE}/accounts/{acc}/orders",
        {"ticker": "aaa", "side": "buy", "quantity": "10", **kw},
        format="json",
    )


def test_flujo_completo_de_practica(ctx):
    c, _, _ = ctx
    r = new_account(c)
    assert r.status_code == 201
    acc = r.json()["account"]["id"]
    o = order(c, acc)
    assert o.status_code == 201 and o.json()["status"] == "FILLED" and o.json()["fill_price"] == "100.100000"
    assert (
        o.json()["reference_source"]["provider"] == "MANUAL"
        and o.json()["adverse_slippage_bps"] == "10.000000"
    )
    s = c.get(f"{BASE}/accounts/{acc}").json()
    assert (
        s["cash"] == "8999.000000"
        and s["positions"][0]["ticker"] == "AAA"
        and "no hay dinero real" in s["notice"]
        and "Ninguna ruta" in s["disclaimer"]
    )
    e = c.get(f"{BASE}/accounts/{acc}/equity").json()
    assert e["curve"][0]["equity"] == "10000.000000" and e["metrics"]["orders"]["FILLED"] == 1
    lst = c.get(f"{BASE}/accounts").json()
    assert [a["id"] for a in lst["accounts"]] == [acc] and "Ninguna ruta" in lst["disclaimer"]


def test_orden_limitada_cancelar_y_procesar(ctx):
    c, _, ins = ctx
    acc = new_account(c).json()["account"]["id"]
    o = order(c, acc, order_type="limit", limit_price="90").json()
    assert o["status"] == "PENDING"
    assert c.post(f"{BASE}/accounts/{acc}/process").json()["still_pending"] == [o["id"]]
    record_price(ins, D(89), "MANUAL", datetime.now(UTC))
    assert c.post(f"{BASE}/accounts/{acc}/process").json()["filled"] == [o["id"]]
    assert c.post(f"{BASE}/accounts/{acc}/orders/{o['id']}/cancel").status_code == 400  # ya ejecutada
    p = order(c, acc, order_type="limit", limit_price="10").json()
    assert c.post(f"{BASE}/accounts/{acc}/orders/{p['id']}/cancel").json()["status"] == "CANCELLED"


@pytest.mark.parametrize(
    "body",
    [
        {"ticker": "ZZZ"},
        {"side": "hold"},
        {"quantity": "0"},
        {"quantity": "x"},
        {"quantity": "1.234"},
        {"order_type": "limit"},
        {"order_type": "stop"},
        {"experiment": 999},
        {"quantity": None},
    ],
)
def test_validaciones_de_orden(ctx, body):
    c, _, _ = ctx
    acc = new_account(c).json()["account"]["id"]
    assert order(c, acc, **body).status_code == 400
    assert PaperOrder.objects.count() == 0


@pytest.mark.parametrize(
    "body",
    [
        {"initial_cash": "5"},
        {"initial_cash": "abc"},
        {"initial_cash": None},
        {"config": {"slippage_bps": 9999}},
        {"config": {"max_position_pct": 5}},
        {"config": {"max_price_age_days": 0}},
    ],
)
def test_validaciones_de_cuenta(ctx, body):
    c, _, _ = ctx
    assert new_account(c, **body).status_code == 400


def test_limite_de_cuentas(ctx):
    c, _, _ = ctx
    for _ in range(10):
        assert new_account(c).status_code == 201
    assert new_account(c).status_code == 400


def test_diario_inmutable_y_ligado_a_ordenes(ctx):
    c, _, _ = ctx
    acc = new_account(c).json()["account"]["id"]
    o = order(c, acc).json()
    r = c.post(
        f"{BASE}/accounts/{acc}/journal",
        {"kind": "DECISION", "text": "Entro por el ranking", "order": o["id"]},
        format="json",
    )
    assert r.status_code == 201 and r.json()["order"] == o["id"]
    assert [e["text"] for e in c.get(f"{BASE}/accounts/{acc}/journal").json()] == ["Entro por el ranking"]
    for bad in (
        {"text": ""},
        {"text": "x" * 3001},
        {"text": "ok", "kind": "otro"},
        {"text": "ok", "order": 9999},
    ):
        assert c.post(f"{BASE}/accounts/{acc}/journal", bad, format="json").status_code == 400
    assert c.put(f"{BASE}/accounts/{acc}/journal", {}, format="json").status_code == 405


def test_propuesta_desde_experimento_y_origen_en_la_orden(ctx):
    c, user, ins = ctx
    acc = new_account(c).json()["account"]["id"]
    exp = QuantExperiment.objects.create(
        user=user, name="e", horizon=20, config={}, dataset_hash="a" * 64, result_hash="b" * 64, quant_version="1", candidate="ridge",
        verdict="NO_PROMOVIDO", result={"latest_scores": [{"instrument": ins.id, "score": 1.0}]},
    )  # fmt: skip
    p = c.get(f"{BASE}/accounts/{acc}/proposal", {"experiment": exp.id, "top_k": 1}).json()
    assert p["proposals"][0]["side"] == "BUY" and any("NO fue promovido" in w for w in p["warnings"])
    o = order(c, acc, experiment=exp.id, quantity=p["proposals"][0]["quantity"]).json()
    assert o["source"] == {"kind": "quant", "experiment": exp.id}
    assert c.get(f"{BASE}/accounts/{acc}/proposal", {"experiment": "x"}).status_code == 400
    assert c.get(f"{BASE}/accounts/{acc}/proposal", {"experiment": 9999}).status_code == 404
    assert c.get(f"{BASE}/accounts/{acc}/proposal", {"experiment": exp.id, "top_k": 99}).status_code == 400


def test_aislamiento_entre_usuarios_y_autenticacion(ctx):
    c, _, _ = ctx
    acc = new_account(c).json()["account"]["id"]
    o = order(c, acc).json()
    otro = APIClient()
    otro.force_authenticate(get_user_model().objects.create_user("beto", password="x"))
    assert otro.get(f"{BASE}/accounts").json()["accounts"] == []
    for method, path in (
        ("get", ""),
        ("get", "/orders"),
        ("post", "/orders"),
        ("get", "/equity"),
        ("get", "/journal"),
        ("post", "/process"),
        ("post", f"/orders/{o['id']}/cancel"),
    ):
        assert getattr(otro, method)(f"{BASE}/accounts/{acc}{path}").status_code == 404, path
    assert APIClient().get(f"{BASE}/accounts").status_code in (401, 403)


def test_comando_procesa_las_pendientes_de_todas_las_cuentas(ctx):
    c, _, ins = ctx
    acc = new_account(c).json()["account"]["id"]
    order(c, acc, order_type="limit", limit_price="90")
    record_price(ins, D(80), "MANUAL", datetime.now(UTC))
    out = StringIO()
    call_command("process_paper_orders", stdout=out)
    assert (
        "1 ejecutadas" in out.getvalue() and PaperAccount.objects.get(pk=acc).orders.get().status == "FILLED"
    )


# ------------------------------------------------------------------ garantía: ninguna ruta ejecuta órdenes reales
FORBIDDEN_IMPORTS = {
    "requests",
    "httpx",
    "urllib",
    "socket",
    "http",
    "subprocess",
    "smtplib",
    "ftplib",
    "websocket",
    "aiohttp",
}
FORBIDDEN_PREFIXES = (
    "modules.portfolio",
    "modules.imports",
    "modules.mcp_access",
    "modules.assistant",
    "modules.fundamentals",
)


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_el_modulo_no_tiene_red_ni_acceso_al_libro_real_ni_a_brokers():
    folder = Path(__file__).parent
    for py in folder.glob("*.py"):
        if py.name.startswith("test_") or py.name == "__init__.py":
            continue
        mods = _imports(py)
        assert not {m.split(".")[0] for m in mods} & FORBIDDEN_IMPORTS, (py.name, mods)
        assert not [m for m in mods if m.startswith(FORBIDDEN_PREFIXES)], (py.name, mods)


def test_las_rutas_de_paper_solo_aceptan_los_metodos_esperados_y_estan_bajo_paper():
    rutas = [str(p.pattern) for p in get_resolver().url_patterns if "paper" in str(p.pattern)]
    assert len(rutas) == 8 and all(r.startswith("api/v1/paper/") for r in rutas)
    for r in rutas:  # ninguna ruta menciona ejecutar/enviar a un broker
        assert not any(w in r for w in ("broker", "execute", "send", "live"))
