from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.imports.models import PositionSnapshot
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy, record_sell
from modules.pricing.services import record_fx, record_price

T0 = datetime(2026, 1, 1, tzinfo=UTC)
REPORT = datetime(2026, 11, 13, tzinfo=UTC)


@pytest.fixture
def ctx(db):
    user = get_user_model().objects.create_user("ana", password="x")
    client = APIClient()
    client.force_authenticate(user)
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    return client, acc


def make_stock(ticker):
    ins = Instrument.objects.create(asset_class="STOCK", name=ticker, quantity_scale=8)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=ticker)
    return ins


def test_dashboard_vacio(ctx):
    client, _ = ctx
    d = client.get("/api/v1/portfolio/dashboard").json()
    assert d["positions"] == [] and d["prices"]["newest_at"] is None and d["totals_by_currency"] == {}
    assert d["fx"] is None


def test_valor_es_titulos_del_libro_por_ultimo_precio(ctx):
    client, acc = ctx
    aaa, bbb = make_stock("AAA"), make_stock("BBB")
    record_buy(account=acc, instrument=aaa, quantity=D(2), price=D(100), executed_at=T0)
    record_buy(account=acc, instrument=bbb, quantity=D(10), price=D(10), executed_at=T0)
    record_price(aaa, D(140), "GBM_REPORT", REPORT)
    record_price(aaa, D(150), "MANUAL", datetime(2026, 11, 14, tzinfo=UTC))  # más reciente gana
    PositionSnapshot.objects.create(account=acc, file_sha256="a" * 64, as_of=REPORT, cash_amount=D(5))
    # BBB sin precio: queda fuera de los totales de mercado.

    d = client.get("/api/v1/portfolio/dashboard").json()
    usd = d["totals_by_currency"]["USD"]
    assert usd["cost_basis"] == "300.00"
    assert usd["market_value"] == "300.00"  # 2 × 150
    assert usd["unrealized_pnl"] == "100.00"  # sobre lo que tiene precio: 300 - 200
    assert usd["unrealized_pct"] == "50.00" and usd["cash"] == "5.00"
    first = d["positions"][0]
    assert first["ticker"] == "AAA" and first["price_provider"] == "MANUAL"
    assert first["weight_pct"] == "100.00" and first["price_age_hours"] > 0
    assert d["unpriced_positions"] == 1
    assert d["prices"]["providers"] == ["MANUAL"]  # solo cuenta el último precio de cada emisora


def test_equivalente_en_mxn_con_tipo_de_cambio_de_referencia(ctx):
    client, acc = ctx
    aaa = make_stock("AAA")
    record_buy(account=acc, instrument=aaa, quantity=D(2), price=D(100), executed_at=T0)
    record_price(aaa, D(150), "MANUAL", REPORT)
    PositionSnapshot.objects.create(account=acc, file_sha256="b" * 64, as_of=REPORT, cash_amount=D(10))
    record_fx("USD", "MXN", D("18.5"), "MANUAL", REPORT)
    fx = client.get("/api/v1/portfolio/dashboard").json()["fx"]
    assert fx["pair"] == "USD/MXN" and fx["total_value_mxn"] == "5735.00"  # (300 + 10) × 18.5
    assert "no es el que aplica el broker" in fx["note"]


def test_dashboard_incluye_realizado_y_aisla_usuarios(ctx):
    client, acc = ctx
    aaa = make_stock("AAA")
    record_buy(account=acc, instrument=aaa, quantity=D(2), price=D(100), executed_at=T0)
    record_sell(account=acc, instrument=aaa, quantity=D(1), price=D(130),
                executed_at=datetime(2026, 2, 1, tzinfo=UTC))  # fmt: skip
    d = client.get("/api/v1/portfolio/dashboard").json()
    assert d["totals_by_currency"]["USD"]["realized_pnl"] == "30.00"
    otro = APIClient()
    otro.force_authenticate(get_user_model().objects.create_user("beto", password="x"))
    assert otro.get("/api/v1/portfolio/dashboard").json()["positions"] == []
    assert APIClient().get("/api/v1/portfolio/dashboard").status_code in (401, 403)


def test_rendimiento_xirr_en_el_dashboard(ctx):
    client, acc = ctx
    aaa = make_stock("AAA")
    record_buy(account=acc, instrument=aaa, quantity=D(10), price=D(100),
               executed_at=datetime(2025, 1, 1, tzinfo=UTC))  # fmt: skip
    record_price(aaa, D(110), "MANUAL", datetime(2026, 1, 1, tzinfo=UTC))  # +10% en 365 días
    perf = client.get("/api/v1/portfolio/dashboard").json()["performance"]["USD"]
    assert perf["reason"] is None and float(perf["xirr_pct"]) == pytest.approx(10.0, abs=0.01)


def test_xirr_no_evaluable_con_saldo_de_apertura(ctx):
    client, acc = ctx
    aaa = make_stock("AAA")
    record_buy(account=acc, instrument=aaa, quantity=D(1), price=D(100), executed_at=T0, origin="OPENING")
    record_price(aaa, D(110), "MANUAL", REPORT)
    perf = client.get("/api/v1/portfolio/dashboard").json()["performance"]["USD"]
    assert perf["xirr_pct"] is None and "apertura" in perf["reason"]
