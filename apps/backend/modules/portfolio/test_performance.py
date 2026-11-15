from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument

from .performance import money_weighted_return, xirr
from .services import record_buy, record_sell

T0 = datetime(2025, 1, 1, tzinfo=UTC)


def test_xirr_un_anio_al_10_por_ciento():
    r = xirr([(T0, -1000.0), (T0 + timedelta(days=365), 1100.0)])
    assert r == pytest.approx(0.10, abs=1e-6)


def test_xirr_con_flujos_intermedios_anula_el_vpn():
    flows = [(T0, -1000.0), (T0 + timedelta(days=100), -500.0), (T0 + timedelta(days=400), 1800.0)]
    r = xirr(flows)
    npv = sum(cf / (1 + r) ** ((t - T0).days / 365) for t, cf in flows)
    assert abs(npv) < 1e-4


def test_xirr_sin_solucion_o_con_pocos_flujos():
    assert xirr([(T0, -100.0)]) is None
    assert xirr([(T0, -100.0), (T0 + timedelta(days=100), -50.0)]) is None  # sin cambio de signo


@pytest.fixture
def ctx(db):
    user = get_user_model().objects.create_user("ana", password="x")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=8)
    return acc, ins


def test_rendimiento_de_operaciones_reales(ctx):
    acc, ins = ctx
    record_buy(account=acc, instrument=ins, quantity=D(10), price=D(100), executed_at=T0)
    out = money_weighted_return([acc.id], "USD", D(1100), T0 + timedelta(days=365), True)
    assert out["reason"] is None and float(out["xirr_pct"]) == pytest.approx(10.0, abs=0.01)


def test_incluye_ventas_y_comisiones(ctx):
    acc, ins = ctx
    record_buy(account=acc, instrument=ins, quantity=D(10), price=D(100), executed_at=T0)
    record_sell(account=acc, instrument=ins, quantity=D(5), price=D(120), fees=D(2),
                executed_at=T0 + timedelta(days=180))  # fmt: skip
    out = money_weighted_return([acc.id], "USD", D(600), T0 + timedelta(days=365), True)
    assert out["reason"] is None and float(out["xirr_pct"]) > 10


def test_no_evaluable_si_hay_saldo_de_apertura(ctx):
    acc, ins = ctx
    record_buy(account=acc, instrument=ins, quantity=D(1), price=D(10), executed_at=T0, origin="OPENING")
    out = money_weighted_return([acc.id], "USD", D(20), T0 + timedelta(days=400), True)
    assert out["xirr_pct"] is None and "apertura" in out["reason"]


def test_no_evaluable_sin_precio_o_con_periodo_corto(ctx):
    acc, ins = ctx
    record_buy(account=acc, instrument=ins, quantity=D(1), price=D(10), executed_at=T0)
    assert "sin precio" in money_weighted_return([acc.id], "USD", None, T0, False)["reason"]
    assert "Periodo menor" in money_weighted_return([acc.id], "USD", D(11), T0 + timedelta(days=5), True)["reason"]
