from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.models import Transaction
from modules.portfolio.services import get_position, record_buy, record_sell

from .models import ImportRow
from .services import ImportError_, confirm_import, preview_import

HEADER = "operation,ticker,quantity,price,fees,currency,executed_at,broker_reference\n"
BUY = "BUY,FIC,3.50,100,0,USD,2026-01-05T15:00:00Z,\n"
SELL = "SELL,FIC,1.25,130,1.00,USD,2026-02-05T15:00:00Z,\n"


def csv_bytes(*rows):
    return (HEADER + "".join(rows)).encode()


@pytest.fixture
def account(db):
    user = get_user_model().objects.create_user("demo", password="x")
    return InvestmentAccount.objects.create(owner=user, name="GBM demo", broker="GBM_USA")


@pytest.fixture
def stock(db):
    ins = Instrument.objects.create(asset_class="STOCK", name="Ficticia", market="NASDAQ")
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="FIC")
    return ins


def do_import(account, *rows):
    batch = preview_import(account, csv_bytes(*rows))
    return batch, confirm_import(batch)


def test_importacion_crea_movimientos_y_posicion(account, stock):
    _, result = do_import(account, BUY, SELL)
    assert result == {"created": 2, "linked": 0, "skipped_duplicates": 0}
    pos = get_position(account, stock)
    assert (pos.quantity, pos.realized_pnl) == (D("2.25"), D("36.50"))
    assert Transaction.objects.filter(origin="IMPORT").count() == 2


def test_mismo_archivo_dos_veces_se_rechaza(account, stock):
    do_import(account, BUY)
    with pytest.raises(ImportError_, match="ya fue cargado"):
        preview_import(account, csv_bytes(BUY))
    assert Transaction.objects.count() == 1


def test_segundo_archivo_con_filas_solapadas_no_duplica(account, stock):
    do_import(account, BUY)
    batch = preview_import(account, csv_bytes(BUY, SELL))  # archivo distinto, repite la compra
    statuses = list(batch.rows.values_list("status", flat=True))
    assert statuses == [ImportRow.Status.DUPLICATE, ImportRow.Status.VALID]
    result = confirm_import(batch)
    assert result["created"] == 1 and result["skipped_duplicates"] == 1
    assert Transaction.objects.count() == 2
    assert get_position(account, stock).quantity == D("2.25")


def test_venta_manual_se_vincula_y_no_se_duplica(account, stock):
    record_buy(account=account, instrument=stock, quantity=D("3.50"), price=D(100),
               executed_at=datetime(2026, 1, 5, 15, tzinfo=UTC))  # fmt: skip
    record_sell(account=account, instrument=stock, quantity=D("1.25"), price=D(130), fees=D("1.00"),
                executed_at=datetime(2026, 2, 5, 15, tzinfo=UTC))  # fmt: skip
    batch = preview_import(account, csv_bytes(BUY, SELL))
    assert {r.status for r in batch.rows.all()} == {ImportRow.Status.MATCH_MANUAL}
    result = confirm_import(batch)
    assert result == {"created": 0, "linked": 2, "skipped_duplicates": 0}
    assert Transaction.objects.count() == 2  # nada nuevo
    pos = get_position(account, stock)
    assert (pos.quantity, pos.realized_pnl) == (D("2.25"), D("36.50"))


def test_filas_invalidas_bloquean_la_confirmacion(account, stock):
    batch = preview_import(account, csv_bytes(BUY, "SELL,ZZZ,1,10,0,USD,2026-02-05T15:00:00Z,\n"))
    bad = batch.rows.get(status=ImportRow.Status.INVALID)
    assert "ticker desconocido" in bad.errors[0]
    with pytest.raises(ImportError_, match="inválidas"):
        confirm_import(batch)
    assert Transaction.objects.count() == 0


def test_venta_sin_saldo_revierte_todo_el_lote(account, stock):
    batch = preview_import(account, csv_bytes(BUY, "SELL,FIC,9,10,0,USD,2026-02-05T15:00:00Z,\n"))
    with pytest.raises(ValueError, match="Saldo insuficiente"):
        confirm_import(batch)
    assert Transaction.objects.count() == 0


def test_fecha_sin_zona_horaria_es_invalida(account, stock):
    batch = preview_import(account, csv_bytes("BUY,FIC,1,10,0,USD,2026-01-05T15:00:00,\n"))
    assert batch.rows.get().status == ImportRow.Status.INVALID


def test_misma_fila_repetida_en_un_archivo_son_dos_operaciones(account, stock):
    _, result = do_import(account, BUY, BUY)
    assert result["created"] == 2
    assert get_position(account, stock).quantity == D("7.00")


def test_referencia_de_broker_existente_es_discrepancia(account, stock):
    do_import(account, "BUY,FIC,1,10,0,USD,2026-01-05T15:00:00Z,REF-1\n")
    batch = preview_import(account, csv_bytes("BUY,FIC,2,11,0,USD,2026-01-06T15:00:00Z,REF-1\n"))
    assert batch.rows.get().status == ImportRow.Status.DISCREPANCY
