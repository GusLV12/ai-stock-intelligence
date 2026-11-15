"""Herramientas MCP de análisis (riesgo, simulación, screener, técnico): solo lectura, con alcance por cuentas y por scope."""

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model

from modules.accounts.models import InvestmentAccount
from modules.fundamentals.screen_models import ScreenRun
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.models import Transaction
from modules.portfolio.services import record_buy
from modules.pricing.history_models import DailyClose
from modules.pricing.services import record_price

from . import tools
from .models import McpAuditLog
from .services import McpError, McpPermissionError, create_credential, run_tool

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def mundo(db):
    user = get_user_model().objects.create_user("ana", password="x")
    a1 = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    a2 = InvestmentAccount.objects.create(owner=user, name="Otra", broker="GBM_USA")
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=8)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    record_buy(account=a1, instrument=ins, quantity=D(2), price=D(100), executed_at=T0)
    record_buy(
        account=a2, instrument=ins, quantity=D(50), price=D(100), executed_at=T0
    )  # cuenta NO autorizada
    record_price(ins, D(150), "MANUAL", datetime.now(UTC) - timedelta(hours=2))
    rng, p, hoy = random.Random(1), 100.0, date.today()
    DailyClose.objects.bulk_create(
        [
            DailyClose(
                instrument=ins,
                date=hoy - timedelta(days=200 - i),
                close=D(str(round(p := p * (1 + rng.gauss(0.0005, 0.01)), 4))),
                currency="USD",
                provider="Y",
            )
            for i in range(200)
        ]
    )
    cred, _ = create_credential(user, "c", ["portfolio:read", "research:read"], [a1])
    return user, a1, a2, ins, cred


def test_el_scope_research_existe_y_es_de_lectura():
    from .services import SCOPES

    assert "research:read" in SCOPES and not any(("write" in s or "trade" in s) for s in SCOPES)


def test_riesgo_solo_ve_las_cuentas_autorizadas(mundo):
    _, _, _, _, cred = mundo
    r = tools.portfolio_risk(cred, 120)
    assert r["evaluable"] is True and r["requested_window_days"] == 120 and "asesoría" in r["disclaimer_mcp"]
    assert list(r["assets"]) == ["AAA"] and r["assets"]["AAA"]["observations"] > 60
    assert r["portfolio"] is not None and "no son una predicción" in " ".join(r["warnings"])


def test_simulacion_usa_solo_las_cuentas_autorizadas(mundo):
    _, _, _, _, cred = mundo
    r = tools.simulate_portfolio(cred, {"shocks": [{"target": "AAA", "pct": -10}]})
    # 2 títulos a 150 = 300 en la cuenta autorizada; −10 % = −30 (no −780 de las 52 unidades totales)
    assert D(r["before_usd"]) == D(300) and D(r["change_usd"]) == D(-30)
    assert [p["ticker"] for p in r["positions"]] == ["AAA"] and "predicción" in r["disclaimer_mcp"]


def test_riesgo_y_simulacion_validan_entradas(mundo):
    _, _, _, _, cred = mundo
    for w in (0, 10, 5000, "x", None):
        with pytest.raises(McpError):
            tools.portfolio_risk(cred, w)
    for bad in (
        "texto",
        None,
        {"preset": "inexistente"},
        {"shocks": [{"target": "AAA", "pct": 5000}]},
        {"shocks": [{"pct": 1}]},
    ):
        with pytest.raises(McpError):
            tools.simulate_portfolio(cred, bad)


def test_screener_no_guarda_nada_y_acota_filas(mundo):
    _user, _, _, _, cred = mundo
    antes = ScreenRun.objects.count()
    r = tools.screen_undervalued_stocks(cred, {"min_fscore": 5})
    assert ScreenRun.objects.count() == antes and "rows" in r and "recomendación" in r["disclaimer_mcp"]
    with pytest.raises(McpError):
        tools.screen_undervalued_stocks(cred, {"min_fscore": 99})
    with pytest.raises(McpError):
        tools.screen_undervalued_stocks(cred, {"as_of": "no-es-fecha"})


def test_analisis_tecnico_devuelve_senales_con_regla_y_antiguedad(mundo):
    _, _, _, _, cred = mundo
    r = tools.technical_analysis(cred, "aaa", 150)
    assert r["evaluable"] is True and r["freshness"]["provider"] == "Y" and len(r["signals"]) >= 6
    assert (
        all({"id", "rule", "fired"} <= set(s) for s in r["signals"]) and "predicciones" in r["disclaimer_mcp"]
    )
    for t, d in (("", 100), ("ZZZ", 100), ("AAA", 5), ("AAA", 5000), ("AAA", "x")):
        with pytest.raises(McpError):
            tools.technical_analysis(cred, t, d)


def test_tecnico_sin_historial_no_inventa_nada(mundo):
    _, _, _, _, cred = mundo
    otro = Instrument.objects.create(asset_class="STOCK", name="SIN", quantity_scale=2)
    InstrumentIdentifier.objects.create(instrument=otro, kind="TICKER", value="SIN")
    r = tools.technical_analysis(cred, "SIN", 100)
    assert r["evaluable"] is False and "refresh_price_history" in r["note"]


def test_los_scopes_se_exigen_y_las_llamadas_se_auditan_sin_modificar_nada(mundo):
    user, a1, _, _, cred = mundo
    solo_cartera, _ = create_credential(user, "cartera", ["portfolio:read"], [a1])
    solo_research, _ = create_credential(user, "research", ["research:read"], [a1])
    # sin research:read no hay screener/técnico; sin portfolio:read no hay riesgo/simulación
    with pytest.raises(McpPermissionError):
        run_tool(
            _key(solo_cartera),
            "technical_analysis",
            "research:read",
            tools.technical_analysis,
            {"ticker": "AAA"},
        )
    with pytest.raises(McpPermissionError):
        run_tool(
            _key(solo_research), "portfolio_risk", "portfolio:read", tools.portfolio_risk, {"window_days": 90}
        )
    antes = (Transaction.objects.count(), ScreenRun.objects.count())
    out = run_tool(_key(cred), "portfolio_risk", "portfolio:read", tools.portfolio_risk, {"window_days": 90})
    assert out["evaluable"] is True and (Transaction.objects.count(), ScreenRun.objects.count()) == antes
    assert McpAuditLog.objects.filter(tool="portfolio_risk", status="ok").count() == 1
    assert McpAuditLog.objects.filter(status="denied").count() == 2


_KEYS = {}


def _key(cred):
    """create_credential devuelve la clave en claro solo al crearla; para estas pruebas se regenera una nueva."""
    from .services import create_credential as mk

    if cred.id not in _KEYS:
        _new, key = mk(cred.owner, f"k{cred.id}", cred.scopes, list(cred.accounts.all()))
        _KEYS[cred.id] = key
    return _KEYS[cred.id]
