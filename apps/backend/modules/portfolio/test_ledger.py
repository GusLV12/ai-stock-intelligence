from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from django.db.models import ProtectedError

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument

from .models import LotDisposal, Transaction
from .services import LedgerError, get_position, record_buy, record_sell

T1 = datetime(2026, 1, 5, 15, tzinfo=UTC)
T2 = datetime(2026, 2, 5, 15, tzinfo=UTC)
T3 = datetime(2026, 3, 5, 15, tzinfo=UTC)


@pytest.fixture
def account(db):
    user = get_user_model().objects.create_user("demo", password="x")
    return InvestmentAccount.objects.create(owner=user, name="GBM demo", broker="GBM_USA")


@pytest.fixture
def stock(db):
    return Instrument.objects.create(asset_class="STOCK", name="Acción ficticia", market="NASDAQ")


def test_venta_parcial_ejemplo_didactico(account, stock):
    """Plan 14.5.4: compra 3.50 @100, venta 1.25 @130 con comisión 1.00."""
    record_buy(account=account, instrument=stock, quantity=D("3.50"), price=D(100), executed_at=T1)
    record_sell(
        account=account, instrument=stock, quantity=D("1.25"), price=D(130),
        fees=D("1.00"), executed_at=T2,
    )  # fmt: skip
    pos = get_position(account, stock)
    assert pos.quantity == D("2.25")
    assert pos.cost_basis == D(225)
    assert pos.average_cost == D(100)
    assert pos.realized_pnl == D("36.50")


def test_venta_total_deja_posicion_cero_y_conserva_historial(account, stock):
    record_buy(account=account, instrument=stock, quantity=D(2), price=D(50), fees=D(1), executed_at=T1)
    record_sell(account=account, instrument=stock, quantity=D(2), price=D(60), fees=D(1), executed_at=T2)
    pos = get_position(account, stock)
    assert pos.quantity == 0
    assert pos.average_cost is None
    assert pos.realized_pnl == D(18)  # (120-1) - (100+1)
    assert Transaction.objects.count() == 2
    assert LotDisposal.objects.count() == 1


def test_fraccion_minima_respeta_precision(account, stock):
    record_buy(account=account, instrument=stock, quantity=D(1), price=D(10), executed_at=T1)
    record_sell(account=account, instrument=stock, quantity=D("0.000001"), price=D(10), executed_at=T2)
    assert get_position(account, stock).quantity == D("0.999999")
    with pytest.raises(LedgerError):
        record_sell(account=account, instrument=stock, quantity=D("0.0000001"), price=D(10), executed_at=T3)


def test_no_se_puede_vender_mas_de_lo_disponible(account, stock):
    record_buy(account=account, instrument=stock, quantity=D(1), price=D(10), executed_at=T1)
    with pytest.raises(LedgerError, match="Saldo insuficiente"):
        record_sell(account=account, instrument=stock, quantity=D("1.5"), price=D(10), executed_at=T2)
    assert Transaction.objects.filter(operation="SELL").count() == 0


def test_fifo_consume_lotes_en_orden_y_reparte_comision(account, stock):
    record_buy(account=account, instrument=stock, quantity=D(1), price=D(100), executed_at=T1)
    record_buy(account=account, instrument=stock, quantity=D(1), price=D(200), executed_at=T2)
    sale = record_sell(
        account=account, instrument=stock, quantity=D("1.5"), price=D(300),
        fees=D(3), executed_at=T3,
    )  # fmt: skip
    d1, d2 = sale.disposals.order_by("id")
    assert (d1.quantity, d1.cost_basis, d1.net_proceeds) == (D(1), D(100), D(298))  # comisión: 3 * 1/1.5 = 2
    assert (d2.quantity, d2.cost_basis, d2.net_proceeds) == (D("0.5"), D(100), D(149))
    assert sum(d.net_proceeds for d in sale.disposals.all()) == D("1.5") * 300 - 3
    pos = get_position(account, stock)
    assert (pos.quantity, pos.cost_basis) == (D("0.5"), D(100))


def test_asientos_no_se_borran(account, stock):
    tx = record_buy(account=account, instrument=stock, quantity=D(1), price=D(10), executed_at=T1)
    with pytest.raises(ProtectedError):
        tx.delete()
    with pytest.raises(ProtectedError):
        Transaction.objects.all().delete()


def test_moneda_debe_coincidir_con_el_instrumento(account, stock):
    with pytest.raises(LedgerError, match="moneda"):
        record_buy(
            account=account, instrument=stock, quantity=D(1), price=D(10),
            currency="MXN", executed_at=T1,
        )  # fmt: skip


def test_no_vende_antes_de_la_compra_ni_reescribe_fifo(account, stock):
    record_buy(account=account, instrument=stock, quantity=D(1), price=D(10), executed_at=T2)
    with pytest.raises(LedgerError, match="Saldo insuficiente"):
        record_sell(account=account, instrument=stock, quantity=D(1), price=D(20), executed_at=T1)
    record_sell(account=account, instrument=stock, quantity=D(1), price=D(20), executed_at=T3)
    with pytest.raises(LedgerError, match="ventas posteriores"):
        record_buy(account=account, instrument=stock, quantity=D(1), price=D(5), executed_at=T1)
