"""Importación y consulta de datos financieros anuales (versionados e idempotentes)."""

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction

from modules.instruments.models import Instrument, InstrumentIdentifier

from .models import NUMERIC_FIELDS, FinancialYear

MAX_ROWS = 2000
NON_NEGATIVE = {"revenue", "total_assets", "current_assets", "current_liabilities", "long_term_debt", "total_debt", "cash", "capex"}
POSITIVE = {"total_assets", "shares_outstanding"}
CSV_COLUMNS = ["ticker", "fiscal_year", "period_end", "published_at", "currency", *NUMERIC_FIELDS]


class FundamentalsError(ValueError):
    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors or []


def _num(value, field):
    if value is None or str(value).strip() == "":
        return None
    try:
        d = Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation:
        raise ValueError(f"{field}: número inválido ({value!r})") from None
    if not d.is_finite():
        raise ValueError(f"{field}: número inválido ({value!r})")
    if field in POSITIVE and d <= 0:
        raise ValueError(f"{field}: debe ser mayor que cero")
    if field in NON_NEGATIVE and d < 0:
        raise ValueError(f"{field}: no puede ser negativo")
    return d


def _date(value, field, required=False):
    if value is None or str(value).strip() == "":
        if required:
            raise ValueError(f"{field}: requerido (AAAA-MM-DD)")
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise ValueError(f"{field}: fecha ISO inválida ({value!r})") from None


def _parse_row(row: dict, lookup) -> dict:
    ticker = str(row.get("ticker", "")).strip().upper()
    instrument = lookup(ticker)
    if instrument is None:
        raise ValueError(f"ticker desconocido: {ticker!r} (créalo primero en instrumentos)")
    try:
        year = int(str(row.get("fiscal_year", "")).strip())
    except ValueError:
        raise ValueError("fiscal_year: entero requerido") from None
    if not 1990 <= year <= date.today().year + 1:
        raise ValueError("fiscal_year fuera de rango")
    period_end = _date(row.get("period_end"), "period_end", required=True)
    published = _date(row.get("published_at"), "published_at")
    if published and published < period_end:
        raise ValueError("published_at no puede ser anterior a period_end")
    values = {f: _num(row.get(f), f) for f in NUMERIC_FIELDS}
    if all(v is None for v in values.values()):
        raise ValueError("la fila no trae ningún dato financiero")
    ca = values["current_assets"]
    if ca is not None and values["total_assets"] is not None and ca > values["total_assets"]:
        raise ValueError("current_assets no puede exceder total_assets")
    if values["gross_profit"] is not None and values["revenue"] is not None and values["gross_profit"] > values["revenue"]:
        raise ValueError("gross_profit no puede exceder revenue")
    currency = (str(row.get("currency") or instrument.currency)).strip().upper()
    return {"instrument": instrument, "fiscal_year": year, "period_end": period_end, "published_at": published,
            "currency": currency, **values}  # fmt: skip


def _lookup():
    cache = {}

    def find(ticker):
        if ticker not in cache:
            ident = InstrumentIdentifier.objects.filter(kind="TICKER", value__iexact=ticker).select_related("instrument").first()
            cache[ticker] = ident.instrument if ident else None
        return cache[ticker]

    return find


def _same(current: FinancialYear, parsed: dict) -> bool:
    keys = ["period_end", "published_at", "currency", *NUMERIC_FIELDS]
    return all(getattr(current, k) == parsed[k] for k in keys)


@transaction.atomic
def import_financials(rows: list[dict], source: str = "MANUAL") -> dict:
    """Todo o nada: si una fila es inválida no se guarda ninguna. Repetir la misma fila no crea versión;
    un valor distinto crea una versión nueva (reexpresión) y conserva la anterior."""
    if not isinstance(rows, list) or not rows:
        raise FundamentalsError("Envía una lista de filas.")
    if len(rows) > MAX_ROWS:
        raise FundamentalsError(f"Máximo {MAX_ROWS} filas por importación.")
    find, errors, parsed_rows = _lookup(), [], []
    for n, row in enumerate(rows, start=1):
        try:
            if not isinstance(row, dict):
                raise TypeError("la fila debe ser un objeto")
            parsed_rows.append(_parse_row(row, find))
        except (ValueError, TypeError) as exc:
            errors.append({"row": n, "error": str(exc)})
    seen = set()
    for n, p in enumerate(parsed_rows, start=1):
        key = (p["instrument"].id, p["fiscal_year"])
        if key in seen:
            errors.append({"row": n, "error": "año fiscal repetido para el mismo instrumento en este archivo"})
        seen.add(key)
    if errors:
        raise FundamentalsError("Hay filas inválidas; no se importó nada.", errors)
    result = {"created": 0, "new_versions": 0, "unchanged": 0}
    for p in parsed_rows:
        latest = FinancialYear.objects.filter(instrument=p["instrument"], fiscal_year=p["fiscal_year"]).order_by("-version").first()
        if latest is None:
            FinancialYear.objects.create(source=source, version=1, **p)
            result["created"] += 1
        elif _same(latest, p):
            result["unchanged"] += 1
        else:
            FinancialYear.objects.create(source=source, version=latest.version + 1, **p)
            result["new_versions"] += 1
    return result


def parse_csv(content: bytes) -> list[dict]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FundamentalsError("El archivo debe ser CSV en UTF-8.") from None
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in ("ticker", "fiscal_year", "period_end") if c not in header]
    if missing:
        raise FundamentalsError("Columnas obligatorias ausentes: " + ", ".join(missing))
    return [{(k or "").strip(): (v or "") for k, v in r.items()} for r in reader]


def latest_years(instrument: Instrument, as_of: date | None = None) -> list[FinancialYear]:
    """Última versión de cada año fiscal, de más reciente a más antiguo. Con `as_of`, solo lo ya público a
    esa fecha (published_at <= as_of; sin fecha de publicación se descarta: no se puede probar que se conocía)."""
    out = {}
    qs = FinancialYear.objects.filter(instrument=instrument).order_by("fiscal_year", "version")
    for fy in qs:
        if as_of and (fy.published_at is None or fy.published_at > as_of):
            continue
        out[fy.fiscal_year] = fy  # la versión más alta (posterior) gana
    return [out[y] for y in sorted(out, reverse=True)]
