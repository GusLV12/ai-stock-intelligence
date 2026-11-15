"""Importación incremental de movimientos desde CSV canónico.

Flujo: preview (valida, detecta duplicados y capturas manuales) -> confirm (transaccional).
Nunca se sustituye la cartera: se agregan movimientos al libro. Reimportar no duplica.
"""

import csv
import hashlib
import io
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction as db_transaction
from django.utils import timezone

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.models import Transaction
from modules.portfolio.services import record_buy, record_sell

from .models import ImportBatch, ImportRow, ManualImportMatch

COLUMNS = ["operation", "ticker", "quantity", "price", "fees", "currency", "executed_at", "broker_reference"]
REQUIRED = ["operation", "ticker", "quantity", "price", "executed_at"]
ADAPTER_VERSION = "1"


class ImportError_(ValueError):
    pass


def _fingerprint(account_id, instrument_id, n: dict, occurrence: int) -> str:
    core = "|".join(
        [
            str(account_id), str(instrument_id), n["operation"], n["executed_at"],
            n["quantity"], n["price"], n["fees"], n["currency"], str(occurrence),
        ]
    )  # fmt: skip
    return hashlib.sha256(core.encode()).hexdigest()


def _s(d: Decimal) -> str:
    """Texto estable de un Decimal (sin notación científica ni ceros sobrantes)."""
    return format(d.normalize(), "f") if d != 0 else "0"


def _dec(value: str, field: str) -> Decimal:
    try:
        d = Decimal(value.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"{field}: número inválido ({value!r})") from exc
    if not d.is_finite():
        raise ValueError(f"{field}: número inválido ({value!r})")
    return d


def _normalize(raw: dict, instrument_lookup) -> tuple[dict, Instrument]:
    missing = [c for c in REQUIRED if not (raw.get(c) or "").strip()]
    if missing:
        raise ValueError("Faltan columnas/valores: " + ", ".join(missing))
    operation = raw["operation"].strip().upper()
    if operation not in ("BUY", "SELL"):
        raise ValueError(f"operation: debe ser BUY o SELL ({raw['operation']!r})")
    instrument = instrument_lookup(raw["ticker"].strip().upper())
    if instrument is None:
        raise ValueError(f"ticker desconocido: {raw['ticker']!r} (créalo primero en instrumentos)")
    quantity = _dec(raw["quantity"], "quantity")
    price = _dec(raw["price"], "price")
    fees = _dec(raw.get("fees") or "0", "fees")
    if quantity <= 0:
        raise ValueError("quantity: debe ser mayor que cero")
    if price < 0 or fees < 0:
        raise ValueError("price/fees: no pueden ser negativos")
    try:
        executed = datetime.fromisoformat(raw["executed_at"].strip())
    except ValueError as exc:
        raise ValueError(f"executed_at: fecha ISO 8601 inválida ({raw['executed_at']!r})") from exc
    if executed.tzinfo is None:
        raise ValueError("executed_at: debe incluir zona horaria (ej. 2026-01-05T15:00:00Z)")
    currency = (raw.get("currency") or instrument.currency).strip().upper()
    normalized = {
        "operation": operation,
        "instrument_id": instrument.id,
        "quantity": _s(quantity),
        "price": _s(price),
        "fees": _s(fees),
        "currency": currency,
        "executed_at": executed.astimezone(UTC).isoformat(),
        "broker_reference": (raw.get("broker_reference") or "").strip(),
    }  # fmt: skip
    return normalized, instrument


def preview_import(account: InvestmentAccount, content: bytes, filename: str = "") -> ImportBatch:
    sha = hashlib.sha256(content).hexdigest()
    if ImportBatch.objects.filter(account=account, file_sha256=sha).exists():
        raise ImportError_("Este archivo ya fue cargado para esta cuenta.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportError_("El archivo debe ser CSV en UTF-8.") from exc
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing_cols = [c for c in REQUIRED if c not in header]
    if missing_cols:
        raise ImportError_("Columnas obligatorias ausentes: " + ", ".join(missing_cols))

    by_ticker: dict[str, Instrument | None] = {}

    def lookup(ticker):
        if ticker not in by_ticker:
            ident = (
                InstrumentIdentifier.objects.filter(kind="TICKER", value__iexact=ticker)
                .select_related("instrument").first()
            )  # fmt: skip
            by_ticker[ticker] = ident.instrument if ident else None
        return by_ticker[ticker]

    with db_transaction.atomic():
        batch = ImportBatch.objects.create(
            account=account, filename=filename[:255], file_sha256=sha, adapter_version=ADAPTER_VERSION
        )
        seen: dict[str, int] = {}
        for number, raw in enumerate(reader, start=2):  # la fila 1 es el encabezado
            raw = {(k or "").strip(): (v or "") for k, v in raw.items()}
            try:
                n, instrument = _normalize(raw, lookup)
            except ValueError as exc:
                ImportRow.objects.create(
                    batch=batch,
                    row_number=number,
                    raw=raw,
                    status=ImportRow.Status.INVALID,
                    errors=[str(exc)],
                )
                continue
            base = _fingerprint(account.id, instrument.id, n, 0)
            seen[base] = seen.get(base, 0) + 1
            fp = _fingerprint(account.id, instrument.id, n, seen[base] - 1)
            status, errors = _classify(account, instrument, n, fp)
            ImportRow.objects.create(
                batch=batch, row_number=number, raw=raw, normalized=n, fingerprint=fp,
                status=status, errors=errors,
            )  # fmt: skip
    return batch


def _classify(account, instrument, n, fp):
    S = ImportRow.Status
    if ImportRow.objects.filter(
        fingerprint=fp, batch__account=account, status__in=[S.CONFIRMED, S.LINKED]
    ).exists():
        return S.DUPLICATE, ["Ya importada en un lote anterior."]
    ref = n["broker_reference"]
    if ref:
        existing = Transaction.objects.filter(account=account, broker_reference=ref).first()
        if existing is not None:
            return S.DISCREPANCY, [f"La referencia {ref} ya existe en el libro (movimiento {existing.id})."]
    manual = _find_manual_match(account, instrument, n)
    if manual is not None:
        return S.MATCH_MANUAL, [f"Coincide con la captura manual {manual.id}; se vinculará sin duplicar."]
    return S.VALID, []


def _find_manual_match(account, instrument, n):
    candidates = Transaction.objects.filter(
        account=account, instrument=instrument, operation=n["operation"], origin="MANUAL",
        import_match__isnull=True, status=Transaction.Status.CONFIRMED,
    )  # fmt: skip
    executed = datetime.fromisoformat(n["executed_at"])
    for tx in candidates:
        if (
            tx.executed_at == executed
            and tx.quantity == Decimal(n["quantity"])
            and tx.price_per_unit == Decimal(n["price"])
            and tx.fees == Decimal(n["fees"])
            and tx.currency == n["currency"]
        ):
            return tx
    return None


@db_transaction.atomic
def confirm_import(batch: ImportBatch) -> dict:
    batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.status == ImportBatch.Status.CONFIRMED:
        raise ImportError_("El lote ya fue confirmado.")
    S = ImportRow.Status
    rows = list(batch.rows.all())
    blocking = [r for r in rows if r.status in (S.INVALID, S.DISCREPANCY)]
    if blocking:
        raise ImportError_(
            "Hay filas inválidas o con discrepancia ("
            + ", ".join(str(r.row_number) for r in blocking)
            + "); "
            "corrige el archivo y vuelve a cargarlo."
        )
    result = {"created": 0, "linked": 0, "skipped_duplicates": 0}
    todo = [r for r in rows if r.status in (S.VALID, S.MATCH_MANUAL)]
    todo.sort(key=lambda r: (r.normalized["executed_at"], r.row_number))
    for row in todo:
        n = row.normalized
        instrument = Instrument.objects.get(pk=n["instrument_id"])
        if row.status == S.MATCH_MANUAL:
            manual = _find_manual_match(batch.account, instrument, n)
            if manual is None:  # ya fue vinculada por otro lote: se trata como duplicada
                row.status, row.errors = S.DUPLICATE, ["La captura manual ya estaba vinculada."]
                row.save(update_fields=["status", "errors"])
                result["skipped_duplicates"] += 1
                continue
            ManualImportMatch.objects.create(manual_transaction=manual, import_row=row)
            row.status, row.transaction = S.LINKED, manual
            row.save(update_fields=["status", "transaction"])
            result["linked"] += 1
            continue
        fn = record_buy if n["operation"] == "BUY" else record_sell
        tx = fn(
            account=batch.account, instrument=instrument, quantity=Decimal(n["quantity"]),
            price=Decimal(n["price"]), fees=Decimal(n["fees"]), currency=n["currency"],
            executed_at=datetime.fromisoformat(n["executed_at"]),
            broker_reference=n["broker_reference"], origin="IMPORT",
        )  # fmt: skip
        row.status, row.transaction = S.CONFIRMED, tx
        row.save(update_fields=["status", "transaction"])
        result["created"] += 1
    result["skipped_duplicates"] = sum(1 for r in rows if r.status == S.DUPLICATE)
    batch.status, batch.confirmed_at = ImportBatch.Status.CONFIRMED, timezone.now()
    batch.save(update_fields=["status", "confirmed_at"])
    return result
