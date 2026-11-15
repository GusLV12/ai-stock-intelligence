from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from .engine import (
    Fill,
    PaperError,
    Rules,
    State,
    apply_fill,
    check_quantity,
    commission,
    equity,
    fill_price,
    replay,
    validate_buy,
    validate_sell,
)

D0 = date(2026, 1, 1)


def f(i, side, qty, price, fee=0, inst=1, day=0):
    return Fill(i, inst, side, D(qty), D(price), D(fee), D0 + timedelta(days=day))


def test_deslizamiento_siempre_en_contra_del_operador():
    assert fill_price(D(100), "BUY", D(5)) == D("100.0500")
    assert fill_price(D(100), "SELL", D(5)) == D("99.9500")
    assert fill_price(D(100), "BUY", D(0)) == D(100)
    with pytest.raises(PaperError):
        fill_price(D(0), "BUY", D(5))


def test_comision_con_minimo():
    r = Rules(commission_bps=D(10), commission_min=D(1))
    assert commission(D(10_000), r) == D(10) and commission(D(100), r) == D(1) and commission(D(0), r) == 0


@pytest.mark.parametrize(
    "qty,scale,ok",
    [
        ("1", 0, True),
        ("1.5", 0, False),
        ("0.123456", 6, True),
        ("0.1234567", 6, False),
        ("0", 2, False),
        ("-1", 2, False),
        ("NaN", 2, False),
        ("1e12", 2, False),
    ],
)
def test_cantidad_y_escala(qty, scale, ok):
    if ok:
        assert check_quantity(D(qty), scale) == D(qty)
    else:
        with pytest.raises(PaperError):
            check_quantity(D(qty), scale)


def test_compra_y_venta_fifo_con_comisiones_a_mano():
    fills = [
        f(1, "BUY", 10, 100, fee=1),
        f(2, "BUY", 10, 120, fee=1, day=1),
        f(3, "SELL", 15, 130, fee=1.5, day=2),
    ]
    s = replay(D(5000), fills)
    # efectivo: 5000 - (1000+1) - (1200+1) + (1950-1.5)
    assert s.cash == D("4746.5")
    # FIFO: 10 del lote a 100 y 5 del lote a 120
    # pnl lote1 = 10*(130-100) - 10*(0.1 + 0.1) = 300 - 2 = 298 ; lote2 = 5*(130-120) - 5*(0.1 + 0.1) = 50 - 1 = 49
    assert s.realized == D(347)
    assert s.positions() == {1: D(5)} and s.cost_basis(1) == D(5) * (D(120) + D("0.1"))
    assert [c["quantity"] for c in s.closed] == [D(10), D(5)] and s.fees == D("3.5")
    assert equity(s, {1: D(130)}) == D("4746.5") + 5 * D(130)


def test_el_orden_cronologico_manda_no_el_de_llegada():
    fills = [f(2, "SELL", 5, 110, day=1), f(1, "BUY", 5, 100, day=0)]
    s = replay(D(1000), fills)
    assert s.positions() == {} and s.realized == D(50)


def test_no_hay_efectivo_negativo_ni_ventas_en_corto():
    with pytest.raises(PaperError, match="Efectivo"):
        replay(D(100), [f(1, "BUY", 2, 100)])
    with pytest.raises(PaperError, match="corto"):
        replay(D(1000), [f(1, "SELL", 1, 100)])
    s = State(cash=D(1000))
    apply_fill(s, f(1, "BUY", 5, 100))
    with pytest.raises(PaperError, match="corto"):
        apply_fill(s, f(2, "SELL", 6, 100))
    assert s.cash == D(500) and s.quantity(1) == 5  # el intento fallido no altera el estado
    with pytest.raises(PaperError):
        apply_fill(s, Fill(3, 1, "HOLD", D(1), D(1), D(0), D0))


def test_validaciones_previas_a_ejecutar():
    r = Rules(max_position_pct=D("0.30"))
    s = State(cash=D(1000))
    validate_buy(s, 1, D(2), D(100), D(0), {}, r)  # 200 de 1000 = 20 %
    with pytest.raises(PaperError, match="30%"):
        validate_buy(s, 1, D(4), D(100), D(0), {}, r)  # 40 %
    with pytest.raises(PaperError, match="Efectivo"):
        validate_buy(s, 1, D(20), D(100), D(0), {}, Rules(max_position_pct=D(1)))
    apply_fill(s, f(1, "BUY", 2, 100))
    validate_sell(s, 1, D(2))
    with pytest.raises(PaperError):
        validate_sell(s, 1, D(3))


def test_equity_sin_precio_usa_el_costo():
    s = replay(D(1000), [f(1, "BUY", 5, 100, fee=5)])
    assert equity(s, {}) == D(495) + D(505)
    assert equity(s, {1: D(90)}) == D(495) + D(450)


def test_reglas_desde_configuracion():
    r = Rules.from_config({"slippage_bps": "7.5", "commission_bps": 2, "max_position_pct": "0.5"})
    assert (
        r.slippage_bps == D("7.5")
        and r.commission_bps == 2
        and r.max_position_pct == D("0.5")
        and r.max_price_age_days == 5
    )
