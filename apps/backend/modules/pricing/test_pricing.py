from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model

from modules.accounts.models import InvestmentAccount
from modules.imports.models import PositionSnapshot, SnapshotLine
from modules.imports.snapshots import apply_opening_balances, import_snapshot
from modules.imports.test_snapshots import AS_OF, ROWS, make_xlsx
from modules.instruments.models import Instrument

from .models import MarketPrice
from .services import PricingError, latest_fx, latest_price, record_fx, record_price

T1 = datetime(2026, 11, 1, 15, tzinfo=UTC)
T2 = datetime(2026, 11, 2, 15, tzinfo=UTC)


@pytest.fixture
def stock(db):
    return Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=8)


def test_precio_idempotente_y_ultimo_por_fecha(stock):
    _, created = record_price(stock, D(10), "MANUAL", T1)
    _, created2 = record_price(stock, D(10), "MANUAL", T1)
    assert created and not created2 and MarketPrice.objects.count() == 1
    record_price(stock, D(12), "MANUAL", T2)
    record_price(stock, D(11), "OTRO", T1)  # observación más vieja llegada después
    assert latest_price(stock).price == D(12)


def test_validaciones_de_precio(stock):
    with pytest.raises(PricingError, match="negativo"):
        record_price(stock, D(-1), "MANUAL", T1)
    with pytest.raises(PricingError, match="zona horaria"):
        record_price(stock, D(1), "MANUAL", datetime(2026, 1, 1))
    with pytest.raises(PricingError, match="moneda"):
        record_price(stock, D(1), "MANUAL", T1, currency="MXN")


def test_fx(db):
    record_fx("usd", "mxn", D("18.25"), "MANUAL", T1)
    record_fx("USD", "MXN", D("18.40"), "MANUAL", T2)
    assert latest_fx("USD", "MXN").rate == D("18.40")
    assert latest_fx("MXN", "USD") is None
    with pytest.raises(PricingError):
        record_fx("USD", "USD", D(1), "MANUAL", T1)
    with pytest.raises(PricingError):
        record_fx("USD", "MXN", D(0), "MANUAL", T1)


def test_precios_del_reporte_se_registran_con_procedencia(db):
    user = get_user_model().objects.create_user("demo", password="x")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    snap = import_snapshot(acc, make_xlsx(ROWS), as_of=AS_OF)
    assert MarketPrice.objects.count() == 0  # las emisoras aún no existen
    apply_opening_balances(snap)
    prices = {p.instrument.name: p for p in MarketPrice.objects.select_related("instrument")}
    assert prices["AAA"].price == D(110) and prices["AAA"].provider == "GBM_REPORT"
    assert prices["AAA"].observed_at == AS_OF
    assert PositionSnapshot.objects.get().lines.count() == SnapshotLine.objects.count() == 2
