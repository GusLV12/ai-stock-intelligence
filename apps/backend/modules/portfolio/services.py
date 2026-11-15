"""Servicios de dominio del libro de inversiones. Todo el dinero y las cantidades usan Decimal.

Reglas: compras crean lotes (comisión incluida en el costo); ventas consumen lotes FIFO,
la comisión de venta reduce el ingreso; no se permite vender más de lo disponible.
Este es el resultado CONTABLE. El cálculo fiscal (MXN, costo promedio actualizado) es otra capa.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db import transaction as db_transaction

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument

from .models import LotDisposal, TaxLot, Transaction

ZERO = Decimal(0)
MONEY_PLACES = Decimal(1).scaleb(-10)


class LedgerError(ValueError):
    pass


def _check_quantity(instrument: Instrument, quantity: Decimal) -> None:
    if quantity <= 0:
        raise LedgerError("La cantidad debe ser mayor que cero.")
    if quantity != quantity.quantize(Decimal(1).scaleb(-instrument.quantity_scale)):
        raise LedgerError(
            f"La cantidad excede la precisión del instrumento ({instrument.quantity_scale} decimales)."
        )


def _check_common(account: InvestmentAccount, instrument: Instrument, currency: str, fees: Decimal):
    if fees < 0:
        raise LedgerError("La comisión no puede ser negativa.")
    if currency != instrument.currency:
        raise LedgerError("La moneda de la operación no coincide con la del instrumento.")


@db_transaction.atomic
def record_buy(
    *,
    account: InvestmentAccount,
    instrument: Instrument,
    quantity: Decimal,
    price: Decimal,
    executed_at: datetime,
    fees: Decimal = ZERO,
    currency: str | None = None,
    broker_reference: str = "",
    origin: str = "MANUAL",
) -> Transaction:
    currency = currency or instrument.currency
    _check_quantity(instrument, quantity)
    _check_common(account, instrument, currency, fees)
    if price < 0:
        raise LedgerError("El precio no puede ser negativo.")
    tx = Transaction.objects.create(
        account=account,
        instrument=instrument,
        operation=Transaction.Operation.BUY,
        quantity=quantity,
        price_per_unit=price,
        fees=fees,
        currency=currency,
        executed_at=executed_at,
        broker_reference=broker_reference,
        origin=origin,
    )
    TaxLot.objects.create(
        transaction=tx,
        account=account,
        instrument=instrument,
        acquired_at=executed_at,
        quantity_acquired=quantity,
        quantity_remaining=quantity,
        total_cost=quantity * price + fees,
        currency=currency,
    )
    return tx


@db_transaction.atomic
def record_sell(
    *,
    account: InvestmentAccount,
    instrument: Instrument,
    quantity: Decimal,
    price: Decimal,
    executed_at: datetime,
    fees: Decimal = ZERO,
    currency: str | None = None,
    broker_reference: str = "",
    origin: str = "MANUAL",
) -> Transaction:
    currency = currency or instrument.currency
    _check_quantity(instrument, quantity)
    _check_common(account, instrument, currency, fees)
    if price < 0:
        raise LedgerError("El precio no puede ser negativo.")

    lots = list(
        TaxLot.objects.select_for_update()
        .filter(account=account, instrument=instrument, quantity_remaining__gt=0)
        .order_by("acquired_at", "id")
    )
    available = sum((lot.quantity_remaining for lot in lots), ZERO)
    if quantity > available:
        raise LedgerError(f"Saldo insuficiente: disponible {available}, solicitado {quantity}.")

    sale = Transaction.objects.create(
        account=account,
        instrument=instrument,
        operation=Transaction.Operation.SELL,
        quantity=quantity,
        price_per_unit=price,
        fees=fees,
        currency=currency,
        executed_at=executed_at,
        broker_reference=broker_reference,
        origin=origin,
    )

    pending = quantity
    fee_left = fees
    for lot in lots:
        if pending == 0:
            break
        take = min(lot.quantity_remaining, pending)
        cost = (lot.total_cost * take / lot.quantity_acquired).quantize(MONEY_PLACES)
        # La comisión se reparte proporcionalmente; el último tramo absorbe el redondeo.
        if take == pending:
            fee_part = fee_left
        else:
            fee_part = (fees * take / quantity).quantize(MONEY_PLACES)
        net = (take * price - fee_part).quantize(MONEY_PLACES)
        LotDisposal.objects.create(sale=sale, lot=lot, quantity=take, cost_basis=cost, net_proceeds=net)
        lot.quantity_remaining -= take
        lot.save(update_fields=["quantity_remaining"])
        pending -= take
        fee_left -= fee_part
    return sale


@dataclass(frozen=True)
class Position:
    quantity: Decimal
    cost_basis: Decimal  # costo contable remanente
    average_cost: Decimal | None
    realized_pnl: Decimal  # ganancia contable realizada acumulada


def get_position(account: InvestmentAccount, instrument: Instrument) -> Position:
    lots = TaxLot.objects.filter(account=account, instrument=instrument, quantity_remaining__gt=0)
    quantity = ZERO
    cost = ZERO
    for lot in lots:
        quantity += lot.quantity_remaining
        cost += lot.total_cost * lot.quantity_remaining / lot.quantity_acquired
    cost = cost.quantize(MONEY_PLACES)
    realized = sum(
        (d.realized_pnl for d in LotDisposal.objects.filter(lot__account=account, lot__instrument=instrument)),
        ZERO,
    )
    average = (cost / quantity).quantize(MONEY_PLACES) if quantity > 0 else None
    return Position(quantity=quantity, cost_basis=cost, average_cost=average, realized_pnl=realized)
