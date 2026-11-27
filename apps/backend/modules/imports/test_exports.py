import json
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import get_position

from .exports import export_csv, export_json
from .services import confirm_import, preview_import

CSV_IN = (
    "operation,ticker,quantity,price,fees,currency,executed_at,broker_reference\n"
    "BUY,FIC,3.50,100,0,USD,2026-01-05T15:00:00Z,R1\n"
    "SELL,FIC,1.25,130,1.00,USD,2026-02-05T15:00:00Z,R2\n"
    "BUY,FIC,0.000001,99.5,0.25,USD,2026-03-05T15:00:00Z,\n"
)


@pytest.fixture
def setup(db):
    user = get_user_model().objects.create_user("demo", password="x")
    a = InvestmentAccount.objects.create(owner=user, name="A", broker="GBM_USA")
    b = InvestmentAccount.objects.create(owner=user, name="B", broker="GBM_USA")
    ins = Instrument.objects.create(asset_class="STOCK", name="Ficticia", market="NASDAQ")
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="FIC")
    return a, b, ins


def test_round_trip_csv_conserva_cantidades_y_resultado(setup):
    a, b, ins = setup
    confirm_import(preview_import(a, CSV_IN.encode()))
    exported = export_csv(a)
    confirm_import(preview_import(b, exported.encode()))  # importar el export en otra cuenta
    pa, pb = get_position(a, ins), get_position(b, ins)
    assert pa == pb
    assert pa.quantity == pb.quantity
    assert export_csv(b) == exported  # mismo contenido tras la vuelta


def test_export_json_versionado(setup):
    a, _, _ = setup
    confirm_import(preview_import(a, CSV_IN.encode()))
    data = json.loads(export_json(a))
    assert data["schema_version"] == 1
    assert len(data["transactions"]) == 3
    assert data["transactions"][0]["quantity"] == "3.5"
    assert data["transactions"][2]["quantity"] == "0.000001"


def test_exportacion_preserva_saldo_de_apertura(setup):
    from datetime import UTC, datetime

    from modules.portfolio.models import Transaction
    from modules.portfolio.services import record_buy

    a, b, ins = setup
    record_buy(
        account=a,
        instrument=ins,
        quantity=D(1),
        price=D(100),
        executed_at=datetime(2026, 1, 1, tzinfo=UTC),
        origin="OPENING",
    )
    confirm_import(preview_import(b, export_csv(a).encode()))
    assert Transaction.objects.get(account=b).origin == "OPENING"
