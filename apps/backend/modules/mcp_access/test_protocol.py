"""Prueba de integración REAL por stdio: lanza el servidor MCP como subproceso (con su propia base
SQLite temporal) y lo usa con el cliente oficial del SDK."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

BACKEND = Path(__file__).resolve().parents[2]
SERVER = BACKEND.parent / "mcp_server" / "server.py"

SEED = """
from datetime import UTC, datetime
from decimal import Decimal as D
from django.contrib.auth import get_user_model
from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy
from modules.pricing.services import record_price
from modules.mcp_access.services import create_credential

user = get_user_model().objects.create_user("ana", password="x")
acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=8)
InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
record_buy(account=acc, instrument=ins, quantity=D(2), price=D(100), executed_at=datetime(2026, 1, 1, tzinfo=UTC))
record_price(ins, D(150), "MANUAL", datetime(2026, 11, 13, tzinfo=UTC))
_, key_ro = create_credential(user, "lectura", ["portfolio:read"], [acc])
_, key_all = create_credential(user, "todo", ["portfolio:read", "transactions:read"], [acc])
_, key_calc = create_credential(user, "calculo", ["calculators:run"], [acc])
print("KEYS", key_ro, key_all, key_calc)
"""


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    db = tmp_path_factory.mktemp("mcp") / "test.sqlite3"
    e = {**os.environ, "SQLITE_PATH": str(db), "DJANGO_DEBUG": "true", "PYTHONUTF8": "1"}
    e.pop("DB_HOST", None)
    e.pop("ASI_MCP_KEY", None)
    py = sys.executable
    subprocess.run(
        [py, "manage.py", "migrate", "-v", "0"], cwd=BACKEND, env=e, check=True, capture_output=True
    )
    out = subprocess.run(
        [py, "manage.py", "shell", "-c", SEED], cwd=BACKEND, env=e, check=True, capture_output=True, text=True
    ).stdout
    line = next(ln for ln in out.splitlines() if ln.startswith("KEYS"))
    _, key_ro, key_all, key_calc = line.split()
    return {"env": e, "ro": key_ro, "all": key_all, "calc": key_calc}


def run(env, key, coro_fn):
    e = {k: v for k, v in env["env"].items() if k != "ASI_MCP_KEY"}
    if key is not None:
        e["ASI_MCP_KEY"] = key
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=e, cwd=str(BACKEND))

    async def main():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return await coro_fn(session)

    return asyncio.run(asyncio.wait_for(main(), timeout=60))


def text(result):
    return " ".join(c.text for c in result.content if hasattr(c, "text"))


def test_descubrimiento_solo_lectura(env):
    async def go(s):
        return (await s.list_tools()).tools

    tools = run(env, env["ro"], go)
    assert {t.name for t in tools} == {
        "portfolio_summary", "list_positions", "get_transactions", "calculate_cagr", "calculate_target_buy_price",
    }  # fmt: skip
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)
    # Ninguna herramienta ESCRIBE u opera: ningún nombre empieza por un verbo de operación.
    assert not any(
        t.name.split("_")[0] in {"buy", "sell", "withdraw", "transfer", "write", "create", "delete"}
        for t in tools
    )
    schema = next(t for t in tools if t.name == "get_transactions").input_schema
    assert set(schema["properties"]) == {"start", "end", "limit"}


def test_llamada_valida_devuelve_datos(env):
    async def go(s):
        return await s.call_tool("list_positions", {})

    res = run(env, env["ro"], go)
    assert not res.is_error
    data = json.loads(text(res))
    assert data["positions"][0]["ticker"] == "AAA" and data["positions"][0]["market_price"] == "150.00"
    assert "asesoría" in data["disclaimer"]


def test_sin_clave_o_clave_invalida_se_rechaza_sin_detalles(env):
    async def go(s):
        return await s.call_tool("portfolio_summary", {})

    for key in (None, "asi_clave-falsa"):
        res = run(env, key, go)
        assert res.is_error and "inválida, vencida o revocada" in text(res)
        assert "AAA" not in text(res)


def test_scope_insuficiente_se_deniega(env):
    async def go(s):
        return await s.call_tool("get_transactions", {})

    async def go_period(s):
        return await s.call_tool("get_transactions", {"start": "2025-12-31", "end": "2026-01-01"})

    res = run(env, env["ro"], go)
    assert res.is_error and "transactions:read" in text(res)
    ok = run(env, env["all"], go_period)
    assert not ok.is_error and json.loads(text(ok))["transactions"][0]["operation"] == "BUY"


def test_parametros_invalidos_son_error_controlado(env):
    async def go(s):
        return await s.call_tool("get_transactions", {"limit": 9999})

    res = run(env, env["all"], go)
    assert res.is_error and "limit" in text(res)


def test_la_revocacion_surte_efecto_en_la_siguiente_llamada(env):
    async def go(s):
        first = await s.call_tool("list_positions", {})
        listing = await asyncio.to_thread(
            lambda: subprocess.run(
                [sys.executable, "manage.py", "list_mcp_keys"], cwd=BACKEND, env=env["env"],
                capture_output=True, text=True, check=True,
            ).stdout
        )  # fmt: skip
        cred_id = next(ln.split("]")[0].strip("[") for ln in listing.splitlines() if "lectura" in ln)
        await asyncio.to_thread(
            lambda: subprocess.run(
                [sys.executable, "manage.py", "revoke_mcp_key", cred_id], cwd=BACKEND, env=env["env"],
                capture_output=True, check=True,
            )
        )  # fmt: skip
        second = await s.call_tool("list_positions", {})
        return first, second

    first, second = run(env, env["ro"], go)
    assert not first.is_error and second.is_error and "revocada" in text(second)


def test_calculadora_por_mcp_y_scope_separado(env):
    async def go(s):
        return await s.call_tool(
            "calculate_cagr", {"mode": "historical", "params": {"initial": 10000, "final": 16105, "years": 5}}
        )

    ok = run(env, env["calc"], go)
    assert not ok.is_error and json.loads(text(ok))["result"]["cagr"].startswith("0.09")
    denied = run(env, env["all"], go)  # una clave de portafolio/movimientos no puede calcular
    assert denied.is_error and "calculators:run" in text(denied)

    async def bad(s):
        return await s.call_tool(
            "calculate_cagr", {"mode": "historical", "params": {"initial": 0, "final": 1, "years": 1}}
        )

    res = run(env, env["calc"], bad)
    assert res.is_error and "mayores que cero" in text(res)

    async def target(s):
        return await s.call_tool(
            "calculate_target_buy_price",
            {"params": {"future_value": "161.051", "years": 5, "target_return": "0.10"}},
        )

    assert json.loads(text(run(env, env["calc"], target)))["result"]["max_price_by_return"] == "100.000000"
