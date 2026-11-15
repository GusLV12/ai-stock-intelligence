from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.imports.models import PositionSnapshot, SnapshotLine
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy, record_sell


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
    assert d["positions"] == [] and d["prices"]["as_of"] is None and d["totals_by_currency"] == {}


def test_dashboard_combina_libro_y_reporte(ctx):
    client, acc = ctx
    aaa, bbb = make_stock("AAA"), make_stock("BBB")
    t = datetime(2026, 1, 1, tzinfo=UTC)
    record_buy(account=acc, instrument=aaa, quantity=D(2), price=D(100), executed_at=t)
    record_buy(account=acc, instrument=bbb, quantity=D(10), price=D(10), executed_at=t)
    snap = PositionSnapshot.objects.create(
        account=acc, file_sha256="a" * 64, as_of=datetime(2026, 11, 13, tzinfo=UTC), cash_amount=D(5)
    )
    SnapshotLine.objects.create(
        snapshot=snap, ticker="AAA", quantity=D(2), average_cost=D(100),
        market_price=D(150), market_value=D(300), raw=[],
    )  # fmt: skip
    # BBB no viene en el reporte: queda sin precio y no distorsiona los totales con valor.

    d = client.get("/api/v1/portfolio/dashboard").json()
    usd = d["totals_by_currency"]["USD"]
    assert usd["cost_basis"] == "300.00"  # 200 + 100
    assert usd["market_value"] == "300.00"
    assert usd["unrealized_pnl"] == "100.00"  # solo sobre lo que tiene precio (300 - 200)
    assert usd["unrealized_pct"] == "50.00" and usd["cash"] == "5.00"
    first = d["positions"][0]
    assert first["ticker"] == "AAA" and first["weight_pct"] == "100.00"
    assert d["unpriced_positions"] == 1
    assert d["prices"]["source"].startswith("Reporte de GBM") and d["prices"]["age_hours"] > 0


def test_dashboard_incluye_realizado_y_aisla_usuarios(ctx):
    client, acc = ctx
    aaa = make_stock("AAA")
    record_buy(account=acc, instrument=aaa, quantity=D(2), price=D(100),
               executed_at=datetime(2026, 1, 1, tzinfo=UTC))  # fmt: skip
    record_sell(account=acc, instrument=aaa, quantity=D(1), price=D(130),
                executed_at=datetime(2026, 2, 1, tzinfo=UTC))  # fmt: skip
    d = client.get("/api/v1/portfolio/dashboard").json()
    assert d["totals_by_currency"]["USD"]["realized_pnl"] == "30.00"
    otro = APIClient()
    otro.force_authenticate(get_user_model().objects.create_user("beto", password="x"))
    assert otro.get("/api/v1/portfolio/dashboard").json()["positions"] == []
    assert APIClient().get("/api/v1/portfolio/dashboard").status_code in (401, 403)
