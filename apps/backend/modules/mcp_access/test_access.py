from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy, record_sell
from modules.pricing.services import record_price

from .models import McpAuditLog, McpCredential
from .services import (
    RATE_LIMIT_PER_MINUTE,
    McpAuthError,
    McpError,
    McpPermissionError,
    McpRateLimited,
    authenticate,
    create_credential,
    revoke,
    run_tool,
)
from .tools import get_transactions, list_positions, portfolio_summary

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def world(db):
    user = get_user_model().objects.create_user("ana", password="x")
    a1 = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    a2 = InvestmentAccount.objects.create(owner=user, name="Otra", broker="GBM_USA")
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=8)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    record_buy(account=a1, instrument=ins, quantity=D(2), price=D(100), executed_at=T0)
    record_buy(account=a2, instrument=ins, quantity=D(50), price=D(100), executed_at=T0)
    record_price(ins, D(150), "MANUAL", datetime(2026, 11, 13, tzinfo=UTC))
    return user, a1, a2


def test_la_clave_se_guarda_solo_como_hash(world):
    user, a1, _ = world
    cred, key = create_credential(user, "c", ["portfolio:read"], [a1])
    assert key.startswith("asi_") and key not in str(cred.__dict__.values())
    assert cred.key_hash != key and len(cred.key_hash) == 64 and cred.prefix == key[:8]
    assert authenticate(key).id == cred.id


@pytest.mark.parametrize("bad", [None, "", "abc", "asi_no-existe", "Bearer asi_x"])
def test_claves_invalidas_dan_el_mismo_error(world, bad):
    with pytest.raises(McpAuthError, match="inválida, vencida o revocada"):
        authenticate(bad)


def test_revocacion_y_expiracion_son_inmediatas(world):
    user, a1, _ = world
    cred, key = create_credential(user, "c", ["portfolio:read"], [a1])
    revoke(cred)
    with pytest.raises(McpAuthError):
        authenticate(key)
    cred2, key2 = create_credential(user, "c2", ["portfolio:read"], [a1])
    McpCredential.objects.filter(pk=cred2.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(McpAuthError):
        authenticate(key2)


def test_validaciones_al_crear(world):
    user, a1, _ = world
    other = get_user_model().objects.create_user("beto", password="x")
    foreign = InvestmentAccount.objects.create(owner=other, name="X", broker="GBM_USA")
    with pytest.raises(ValueError, match="desconocidos"):
        create_credential(user, "c", ["trade:write"], [a1])
    with pytest.raises(ValueError, match="al menos un scope"):
        create_credential(user, "c", [], [a1])
    with pytest.raises(ValueError, match="al menos una cuenta"):
        create_credential(user, "c", ["portfolio:read"], [])
    with pytest.raises(ValueError, match="propias"):
        create_credential(user, "c", ["portfolio:read"], [foreign])
    with pytest.raises(ValueError, match="vigencia"):
        create_credential(user, "c", ["portfolio:read"], [a1], days=0)


def test_solo_ve_las_cuentas_autorizadas(world):
    user, a1, _ = world
    _, key = create_credential(user, "c", ["portfolio:read"], [a1])
    pos = run_tool(key, "list_positions", "portfolio:read", list_positions)["positions"]
    assert len(pos) == 1 and pos[0]["quantity"] == "2"  # no suma las 50 de la cuenta no autorizada
    summary = run_tool(key, "portfolio_summary", "portfolio:read", portfolio_summary)
    assert summary["totals_by_currency"]["USD"]["cost_basis"] == "200.00"
    assert "asesoría" in summary["disclaimer"] and summary["prices"]["providers"] == ["MANUAL"]


def test_scope_insuficiente_se_deniega_y_se_audita(world):
    user, a1, _ = world
    _, key = create_credential(user, "c", ["portfolio:read"], [a1])
    with pytest.raises(McpPermissionError, match="transactions:read"):
        run_tool(key, "get_transactions", "transactions:read", get_transactions)
    log = McpAuditLog.objects.get()
    assert log.status == "denied" and log.tool == "get_transactions"


def test_auditoria_de_exito_fallo_de_auth_y_sin_secretos(world):
    user, a1, _ = world
    _, key = create_credential(user, "c", ["portfolio:read"], [a1])
    run_tool(key, "list_positions", "portfolio:read", list_positions)
    with pytest.raises(McpAuthError):
        run_tool("asi_mala", "list_positions", "portfolio:read", list_positions)
    ok, bad = McpAuditLog.objects.order_by("id")
    assert ok.status == "ok" and ok.owner == user and ok.credential_prefix == key[:8]
    assert bad.status == "auth_failed" and bad.credential is None
    assert all(key not in str(x.__dict__) and "asi_mala" not in str(x.__dict__) for x in (ok, bad))


def test_limite_de_uso_por_minuto(world):
    user, a1, _ = world
    _, key = create_credential(user, "c", ["portfolio:read"], [a1])
    for _ in range(RATE_LIMIT_PER_MINUTE):
        run_tool(key, "list_positions", "portfolio:read", list_positions)
    with pytest.raises(McpRateLimited):
        run_tool(key, "list_positions", "portfolio:read", list_positions)


def test_get_transactions_acotado_y_validado(world):
    user, a1, _ = world
    _, key = create_credential(user, "c", ["transactions:read"], [a1])
    record_sell(account=a1, instrument=Instrument.objects.get(name="AAA"), quantity=D(1), price=D(120),
                executed_at=timezone.now() - timedelta(days=5))  # fmt: skip

    def call(**p):
        return run_tool(key, "get_transactions", "transactions:read", get_transactions, p)

    res = call(start=(timezone.now() - timedelta(days=30)).isoformat())
    assert [t["operation"] for t in res["transactions"]] == ["SELL"]  # la compra de enero queda fuera
    assert res["transactions"][0]["account"] == "GBM"
    assert len(call(start="2025-12-31", end="2026-02-01")["transactions"]) == 1
    for bad in ({"limit": 0}, {"limit": 201}, {"start": "ayer"}, {"start": "2026-02-01", "end": "2026-01-01"},
                {"start": "2020-01-01", "end": "2026-01-01"}):  # fmt: skip
        with pytest.raises(McpError):
            call(**bad)


def test_error_interno_no_filtra_detalles(world):
    user, a1, _ = world
    _, key = create_credential(user, "c", ["portfolio:read"], [a1])

    def boom(cred):
        raise RuntimeError("secreto interno de la base de datos")

    with pytest.raises(McpError) as exc:
        run_tool(key, "list_positions", "portfolio:read", boom)
    assert "secreto" not in str(exc.value)
    assert McpAuditLog.objects.get().detail == "error interno"


def test_comandos_de_gestion(world):
    out = StringIO()
    call_command(
        "create_mcp_key", user="ana", name="Claude Code", scopes="portfolio:read", accounts="GBM", stdout=out
    )
    key = out.getvalue().strip().splitlines()[-1]
    assert authenticate(key).name == "Claude Code"
    listing = StringIO()
    call_command("list_mcp_keys", stdout=listing)
    assert "activa" in listing.getvalue() and key not in listing.getvalue()
    cred = McpCredential.objects.get()
    call_command("revoke_mcp_key", cred.id, stdout=StringIO())
    with pytest.raises(McpAuthError):
        authenticate(key)
    with pytest.raises(CommandError):
        call_command("create_mcp_key", user="ana", name="x", scopes="trade:write", accounts="GBM")


def test_end_sin_hora_incluye_todo_ese_dia(world):
    user, a1, _ = world
    _, key = create_credential(user, "c", ["transactions:read"], [a1])
    # La compra es del 1 ene 2026 00:00 UTC = 31 dic 2025 18:00 en México.
    res = run_tool(key, "get_transactions", "transactions:read", get_transactions,
                   {"start": "2025-12-31", "end": "2025-12-31"})  # fmt: skip
    assert len(res["transactions"]) == 1
    assert run_tool(key, "get_transactions", "transactions:read", get_transactions,
                    {"start": "2026-01-02", "end": "2026-01-03"})["transactions"] == []  # fmt: skip


def test_scope_de_calculadoras_no_lee_la_cartera_y_se_audita(world):
    from .tools import calculate_cagr

    user, a1, _ = world
    _, key = create_credential(user, "calc", ["calculators:run"], [a1])
    res = run_tool(key, "calculate_cagr", "calculators:run", calculate_cagr,
                   {"mode": "real", "params": {"nominal": "0.10", "inflation": "0.04"}})  # fmt: skip
    assert res["result"]["real_return"] == "0.057692"
    with pytest.raises(McpError, match="mayores que cero"):
        run_tool(key, "calculate_cagr", "calculators:run", calculate_cagr,
                 {"mode": "historical", "params": {"initial": 0, "final": 1, "years": 1}})  # fmt: skip
    assert [s for s in McpAuditLog.objects.order_by("id").values_list("status", flat=True)] == ["ok", "error"]
    with pytest.raises(McpPermissionError):  # una clave de calculadora no puede leer la cartera
        run_tool(key, "list_positions", "portfolio:read", list_positions)
