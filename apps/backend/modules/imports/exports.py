"""Exportación de movimientos en el mismo formato canónico que se importa (ida y vuelta)."""

import csv
import io
import json
from datetime import UTC

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import InstrumentIdentifier
from modules.portfolio.models import Transaction

from .services import COLUMNS, _s


def _rows(account: InvestmentAccount):
    txs = (
        Transaction.objects.filter(account=account, status=Transaction.Status.CONFIRMED)
        .select_related("instrument")
        .order_by("executed_at", "id")
    )
    tickers = {
        i.instrument_id: i.value
        for i in InstrumentIdentifier.objects.filter(
            kind="TICKER", instrument__in={t.instrument for t in txs}
        )
    }
    for t in txs:
        yield {
            "operation": t.operation,
            "ticker": tickers.get(t.instrument_id, ""),
            "quantity": _s(t.quantity),
            "price": _s(t.price_per_unit),
            "fees": _s(t.fees),
            "currency": t.currency,
            "executed_at": t.executed_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "broker_reference": t.broker_reference,
            "origin": t.origin,
        }


def export_csv(account: InvestmentAccount) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(_rows(account))
    return buf.getvalue()


def export_json(account: InvestmentAccount) -> str:
    payload = {
        "schema_version": 1,
        "account": {"name": account.name, "broker": account.broker, "base_currency": account.base_currency},
        "transactions": list(_rows(account)),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
