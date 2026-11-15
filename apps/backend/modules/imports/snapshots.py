"""Snapshots de posiciones del broker: importación, conciliación y saldo de apertura explícito."""

from datetime import datetime
from decimal import Decimal

from django.db import transaction as db_transaction
from django.utils import timezone

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.models import TaxLot
from modules.portfolio.services import get_position, record_buy
from modules.pricing.services import record_price

from .gbm_positions import as_of_from_filename, parse_portfolio_xlsx
from .models import PositionSnapshot, SnapshotLine
from .services import ImportError_

QTY_TOLERANCE = Decimal("0.00000001")  # GBM reporta títulos con 8 decimales
COST_TOLERANCE = Decimal("0.005")  # costo promedio reportado a centavos
GBM_QUANTITY_SCALE = 8


def _find_instrument(ticker: str) -> Instrument | None:
    ident = (
        InstrumentIdentifier.objects.filter(kind="TICKER", value__iexact=ticker)
        .select_related("instrument")
        .first()
    )
    return ident.instrument if ident else None


@db_transaction.atomic
def import_snapshot(
    account: InvestmentAccount, content: bytes, filename: str = "", as_of: datetime | None = None
) -> PositionSnapshot:
    if account.broker != InvestmentAccount.Broker.GBM_USA:
        raise ImportError_("Este formato solo aplica a cuentas de GBM Trading USA.")
    parsed = parse_portfolio_xlsx(content)
    if PositionSnapshot.objects.filter(account=account, file_sha256=parsed["sha256"]).exists():
        raise ImportError_("Este archivo ya fue cargado para esta cuenta.")
    as_of = as_of or as_of_from_filename(filename)
    if as_of is None:
        raise ImportError_("Indica la fecha del reporte (as_of); el nombre del archivo no la trae.")
    if as_of.tzinfo is None:
        raise ImportError_("La fecha del reporte debe incluir zona horaria.")
    snap = PositionSnapshot.objects.create(
        account=account, filename=filename[:255], file_sha256=parsed["sha256"], as_of=as_of,
        cash_amount=parsed["cash"],
    )  # fmt: skip
    SnapshotLine.objects.bulk_create(SnapshotLine(snapshot=snap, **ln) for ln in parsed["lines"])
    ingest_prices(snap)
    return snap


def ingest_prices(snapshot: PositionSnapshot) -> int:
    """Registra los precios del reporte como observaciones (proveedor GBM_REPORT, hora del reporte).
    Solo para instrumentos que ya existen; es idempotente."""
    count = 0
    for line in snapshot.lines.all():
        ins = _find_instrument(line.ticker)
        if ins is None or line.market_price is None:
            continue
        _, created = record_price(ins, line.market_price, "GBM_REPORT", snapshot.as_of)
        count += int(created)
    return count


def reconcile(snapshot: PositionSnapshot) -> list[dict]:
    """Compara lo reportado con lo que el libro calcula. Estados: ok, diferencia, falta_en_libro,
    falta_en_reporte (plan 14.3). Un snapshot sin historial dará 'falta_en_libro' en todo."""
    account = snapshot.account
    out, seen = [], set()
    for line in snapshot.lines.all():
        ins = _find_instrument(line.ticker)
        row = {
            "ticker": line.ticker,
            "reported_quantity": line.quantity,
            "reported_average_cost": line.average_cost,
            "ledger_quantity": Decimal(0),
            "ledger_average_cost": None,
            "quantity_diff": line.quantity,
            "status": "falta_en_libro",
        }
        if ins is not None:
            seen.add(ins.id)
            pos = get_position(account, ins)
            row.update(
                ledger_quantity=pos.quantity,
                ledger_average_cost=pos.average_cost,
                quantity_diff=line.quantity - pos.quantity,
            )
            if pos.quantity > 0:
                cost_ok = (
                    line.average_cost is None
                    or pos.average_cost is None
                    or abs(line.average_cost - pos.average_cost) <= COST_TOLERANCE
                )
                same_qty = abs(row["quantity_diff"]) <= QTY_TOLERANCE
                row["status"] = "ok" if same_qty and cost_ok else "diferencia"
        out.append(row)
    for pair in TaxLot.objects.filter(account=account, quantity_remaining__gt=0).values_list(
        "instrument_id", flat=True
    ).distinct():
        if pair in seen:
            continue
        ins = Instrument.objects.get(pk=pair)
        pos = get_position(account, ins)
        out.append(
            {
                "ticker": ins.name, "reported_quantity": Decimal(0), "reported_average_cost": None,
                "ledger_quantity": pos.quantity, "ledger_average_cost": pos.average_cost,
                "quantity_diff": -pos.quantity, "status": "falta_en_reporte",
            }
        )  # fmt: skip
    return out


@db_transaction.atomic
def apply_opening_balances(snapshot: PositionSnapshot) -> dict:
    """Crea lotes de apertura con el costo promedio reportado. SOLO para emisoras sin posición en el
    libro (nunca suma sobre historial existente). El lote queda con origen OPENING: la fecha de
    adquisición real se desconoce y el costo es el promedio redondeado a centavos."""
    snapshot = PositionSnapshot.objects.select_for_update().get(pk=snapshot.pk)
    if snapshot.opening_applied_at:
        raise ImportError_("El saldo de apertura de este reporte ya fue aplicado.")
    created, skipped = [], []
    for line in snapshot.lines.all():
        ins = _find_instrument(line.ticker)
        if ins is not None and get_position(snapshot.account, ins).quantity > 0:
            skipped.append({"ticker": line.ticker, "reason": "ya tiene posición en el libro"})
            continue
        if line.average_cost is None or line.quantity <= 0:
            skipped.append({"ticker": line.ticker, "reason": "sin costo promedio o cantidad cero"})
            continue
        if ins is None:
            ins = Instrument.objects.create(
                asset_class="STOCK", name=line.ticker, market="USA (GBM)", currency="USD",
                quantity_scale=GBM_QUANTITY_SCALE,
            )  # fmt: skip
            InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=line.ticker)
        elif ins.quantity_scale < GBM_QUANTITY_SCALE:
            ins.quantity_scale = GBM_QUANTITY_SCALE
            ins.save(update_fields=["quantity_scale"])
        record_buy(
            account=snapshot.account, instrument=ins, quantity=line.quantity, price=line.average_cost,
            executed_at=snapshot.as_of, origin="OPENING",
            broker_reference=f"OPENING-{snapshot.id}-{line.ticker}",
        )  # fmt: skip
        created.append(line.ticker)
    snapshot.opening_applied_at = timezone.now()
    snapshot.save(update_fields=["opening_applied_at"])
    ingest_prices(snapshot)  # ahora existen las emisoras nuevas
    return {"created": created, "skipped": skipped}
