from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.imports.models import PositionSnapshot, SnapshotLine
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.models import Transaction
from modules.portfolio.services import record_buy


@pytest.mark.django_db
def test_reporte_refleja_reduccion_y_ausencia_sin_crear_ventas():
    user = get_user_model().objects.create_user("prueba")
    account = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    for ticker in ("AAA", "BBB"):
        ins = Instrument.objects.create(asset_class="STOCK", name=ticker, quantity_scale=8)
        InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=ticker)
        record_buy(
            account=account,
            instrument=ins,
            quantity=D(10),
            price=D(5),
            executed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    old = PositionSnapshot.objects.create(
        account=account, file_sha256="a" * 64, as_of=datetime(2026, 1, 2, tzinfo=UTC)
    )
    latest = PositionSnapshot.objects.create(
        account=account, file_sha256="b" * 64, as_of=datetime(2026, 1, 3, tzinfo=UTC), cash_amount=D(5)
    )
    for ticker in ("AAA", "BBB"):
        SnapshotLine.objects.create(
            snapshot=old, ticker=ticker, quantity=D(10), average_cost=D(5), market_price=D(6), raw=[]
        )
    SnapshotLine.objects.create(
        snapshot=latest, ticker="AAA", quantity=D(4), average_cost=D(5), market_price=D(7), raw=[]
    )
    client = APIClient()
    client.force_authenticate(user)
    result = client.get("/api/v1/portfolio/dashboard?source=report").json()
    assert [(p["ticker"], p["quantity"]) for p in result["positions"]] == [("AAA", "4")]
    assert {c["ticker"]: c["kind"] for c in result["report"]["changes"]} == {
        "AAA": "disminucion",
        "BBB": "ausente",
    }
    assert result["totals_by_currency"]["USD"]["market_value"] == "28.00"
    assert result["totals_by_currency"]["USD"]["realized_pnl"] is None
    assert result["performance"]["USD"]["xirr_pct"] is None
    assert Transaction.objects.filter(operation="SELL").count() == 0
    ledger = client.get("/api/v1/portfolio/dashboard").json()
    assert {p["ticker"] for p in ledger["positions"]} == {"AAA", "BBB"}
    other = APIClient()
    other.force_authenticate(get_user_model().objects.create_user("otra"))
    assert other.get("/api/v1/portfolio/dashboard?source=report").json()["positions"] == []


@pytest.mark.django_db
def test_reporte_incompleto_no_muestra_total_parcial_como_total_completo():
    user = get_user_model().objects.create_user("prueba")
    account = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    snap = PositionSnapshot.objects.create(
        account=account, file_sha256="c" * 64, as_of=datetime(2026, 1, 3, tzinfo=UTC)
    )
    SnapshotLine.objects.create(
        snapshot=snap, ticker="AAA", quantity=D(1), average_cost=None, market_price=None, raw=[]
    )
    client = APIClient()
    client.force_authenticate(user)
    result = client.get("/api/v1/portfolio/dashboard?source=report").json()
    assert result["totals_by_currency"]["USD"]["market_value"] is None
    assert result["totals_by_currency"]["USD"]["cost_basis"] is None
    assert result["unpriced_positions"] == 1
