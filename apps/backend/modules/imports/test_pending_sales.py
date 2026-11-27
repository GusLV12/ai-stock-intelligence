from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.portfolio.models import Transaction

from .models import PositionSnapshot, SnapshotLine
from .pending import pending_sales


@pytest.mark.django_db
def test_deteccion_de_ausencias_y_reducciones_sin_ventas():
    user = get_user_model().objects.create_user("ana")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    for day, items in [(1, {"AAA": "5", "BBB": "4"}), (2, {"AAA": "2", "CCC": "1"})]:
        snapshot = PositionSnapshot.objects.create(
            account=acc, file_sha256=str(day) * 64, as_of=datetime(2026, 1, day, tzinfo=UTC)
        )
        for ticker, quantity in items.items():
            SnapshotLine.objects.create(snapshot=snapshot, ticker=ticker, quantity=Decimal(quantity), raw=[])
    pending = pending_sales(acc)
    assert [(p["ticker"], p["kind"], Decimal(p["quantity"])) for p in pending] == [
        ("AAA", "parcial", Decimal(3)),
        ("BBB", "total", Decimal(4)),
    ]
    assert not Transaction.objects.exists()
    client = APIClient()
    client.force_authenticate(user)
    assert len(client.get(f"/api/v1/imports/pending-sales?account={acc.id}").json()["pending"]) == 2
    client.force_authenticate(get_user_model().objects.create_user("otra"))
    assert client.get(f"/api/v1/imports/pending-sales?account={acc.id}").status_code == 400


@pytest.mark.django_db
def test_rechaza_comparacion_con_fechas_empatadas():
    user = get_user_model().objects.create_user("ana")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    for index in range(2):
        snap = PositionSnapshot.objects.create(
            account=acc, file_sha256=str(index) * 64, as_of=datetime(2026, 1, 1, tzinfo=UTC)
        )
        SnapshotLine.objects.create(snapshot=snap, ticker="AAA", quantity=Decimal(2 - index), raw=[])
    assert pending_sales(acc) == []
