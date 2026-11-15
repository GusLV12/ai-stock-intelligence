import io
from datetime import UTC, datetime
from decimal import Decimal as D

import openpyxl
import pytest
from django.contrib.auth import get_user_model

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.fiscal import sales_for_year
from modules.portfolio.services import get_position, record_buy, record_sell

from .gbm_positions import as_of_from_filename, money, parse_portfolio_xlsx
from .services import ImportError_
from .snapshots import apply_opening_balances, import_snapshot, reconcile

HEADER = ["Emisora/Fondo", "Títulos", "Costo promedio", "Precio mercado", "PPP", "Valor mercado",
          "P / M", "% Var. Hist.", "% Var. Dia.", "Imp X Cto", "% Cartera"]  # fmt: skip
AS_OF = datetime(2026, 11, 13, 23, 38, tzinfo=UTC)


def make_xlsx(rows, cash="$0.010"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Mercado de Capitales USA"])
    ws.append(HEADER)
    for r in rows:
        ws.append(r)
    ws.append([])
    ws.append(["Liquidez"])
    ws.append(HEADER)
    ws.append(["efectivo", "-", "-", "-", "-", cash, "-", "-", "-", "-", "0.00%"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ROWS = [
    ["AAA", 0.74298378, "$100.00", "$110.00", "-", "$81.73", "$7.43", "10%", "-1%", "$74.30", "1%"],
    ["BBB", 5.5, "$20.50", "$19.00", "-", "$104.50", "-$8.25", "-7%", "0%", "$112.75", "2%"],
]


@pytest.fixture
def account(db):
    user = get_user_model().objects.create_user("demo", password="x")
    return InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")


def test_money_y_fecha_del_nombre():
    assert money("$1,418.18") == D("1418.18")
    assert money("-$42.53") == D("-42.53")
    assert money("-") is None and money(None) is None
    assert as_of_from_filename("App_GBM_Detalle_Portafolio_USA_1794613125783.xlsx") == datetime(
        2026, 11, 13, 23, 38, 45, tzinfo=UTC
    )


def test_parser_lee_posiciones_y_efectivo():
    parsed = parse_portfolio_xlsx(make_xlsx(ROWS))
    assert [ln["ticker"] for ln in parsed["lines"]] == ["AAA", "BBB"]
    assert parsed["lines"][0]["quantity"] == D("0.74298378")  # sin ruido de float
    assert parsed["lines"][1]["average_cost"] == D("20.50")
    assert parsed["cash"] == D("0.010")


def test_formato_desconocido_se_rechaza():
    wb = openpyxl.Workbook()
    wb.active.append(["otra", "cosa"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ImportError_, match="Formato no reconocido"):
        parse_portfolio_xlsx(buf.getvalue())
    with pytest.raises(ImportError_):
        parse_portfolio_xlsx(b"no es un xlsx")


def test_snapshot_sin_historial_da_falta_en_libro_y_no_crea_movimientos(account):
    content = make_xlsx(ROWS)  # una sola vez: openpyxl embebe la hora de creación en el archivo
    snap = import_snapshot(account, content, as_of=AS_OF)
    assert {r["status"] for r in reconcile(snap)} == {"falta_en_libro"}
    assert account.transactions.count() == 0
    with pytest.raises(ImportError_, match="ya fue cargado"):
        import_snapshot(account, content, as_of=AS_OF)


def test_saldo_de_apertura_crea_lotes_y_concilia_ok(account):
    snap = import_snapshot(account, make_xlsx(ROWS), as_of=AS_OF)
    result = apply_opening_balances(snap)
    assert sorted(result["created"]) == ["AAA", "BBB"] and result["skipped"] == []
    ins = Instrument.objects.get(name="AAA")
    assert ins.quantity_scale == 8
    pos = get_position(account, ins)
    assert (pos.quantity, pos.average_cost) == (D("0.74298378"), D(100))
    assert {r["status"] for r in reconcile(snap)} == {"ok"}
    with pytest.raises(ImportError_, match="ya fue aplicado"):
        apply_opening_balances(snap)


def test_apertura_no_suma_sobre_posicion_existente(account):
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=8)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    record_buy(account=account, instrument=ins, quantity=D(1), price=D(90), executed_at=AS_OF)
    snap = import_snapshot(account, make_xlsx(ROWS), as_of=AS_OF)
    result = apply_opening_balances(snap)
    assert result["created"] == ["BBB"]
    assert result["skipped"][0]["ticker"] == "AAA"
    assert get_position(account, ins).quantity == D(1)  # intacta
    rec = {r["ticker"]: r for r in reconcile(snap)}
    assert rec["AAA"]["status"] == "diferencia"


def test_conciliacion_detecta_posicion_que_el_reporte_no_trae(account):
    ins = Instrument.objects.create(asset_class="STOCK", name="ZZZ", quantity_scale=8)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="ZZZ")
    record_buy(account=account, instrument=ins, quantity=D(2), price=D(10), executed_at=AS_OF)
    snap = import_snapshot(account, make_xlsx(ROWS), as_of=AS_OF)
    assert any(r["status"] == "falta_en_reporte" and r["ticker"] == "ZZZ" for r in reconcile(snap))


def test_venta_sobre_lote_de_apertura_queda_marcada_en_el_centro_fiscal(account):
    snap = import_snapshot(account, make_xlsx(ROWS), as_of=AS_OF)
    apply_opening_balances(snap)
    ins = Instrument.objects.get(name="BBB")
    record_sell(account=account, instrument=ins, quantity=D("1.5"), price=D(25),
                executed_at=datetime(2026, 12, 1, tzinfo=UTC))  # fmt: skip
    sale = sales_for_year(account.owner, 2026)[0]
    assert any("costo de apertura" in p for p in sale["pending"])
    assert sale["realized_pnl_book"] == D("1.5") * (25 - D("20.5"))


def test_solo_cuentas_gbm(db):
    user = get_user_model().objects.create_user("x", password="x")
    mp = InvestmentAccount.objects.create(owner=user, name="MP", broker="MERCADO_PAGO")
    with pytest.raises(ImportError_, match="GBM"):
        import_snapshot(mp, make_xlsx(ROWS), as_of=AS_OF)
