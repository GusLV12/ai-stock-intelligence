"""Lector del XLSX "Detalle de portafolio USA" de la app de GBM.

Formato observado: hoja con la sección "Mercado de Capitales USA" (encabezado en la fila con
"Emisora/Fondo"), una fila por emisora, y la sección "Liquidez" con el efectivo. Los importes
vienen como texto ("$1,418.18", "-$42.53", "-"); los títulos como número con hasta 8 decimales.
"""

import hashlib
import io
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import openpyxl

from .services import ImportError_

HEADER_KEY = "emisora/fondo"
FILENAME_EPOCH = re.compile(r"_(\d{13})\.xlsx$", re.IGNORECASE)


def money(value) -> Decimal | None:
    """'$1,418.18' -> Decimal; '-$42.53' -> Decimal('-42.53'); '-' o vacío -> None."""
    if value is None:
        return None
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    text = str(value).strip()
    if text in ("", "-", "—"):
        return None
    negative = text.startswith(("-", "("))
    cleaned = re.sub(r"[^\d.]", "", text)
    if not cleaned:
        return None
    try:
        d = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ImportError_(f"Importe ilegible: {value!r}") from exc
    return -d if negative else d


def quantity(value) -> Decimal:
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    try:
        # str(float) conserva la representación más corta (ej. 0.74298378), sin ruido binario.
        d = Decimal(str(value))
    except InvalidOperation as exc:
        raise ImportError_(f"Cantidad de títulos ilegible: {value!r}") from exc
    if not d.is_finite() or d < 0:
        raise ImportError_(f"Cantidad de títulos inválida: {value!r}")
    return d


def as_of_from_filename(filename: str) -> datetime | None:
    m = FILENAME_EPOCH.search(filename or "")
    return datetime.fromtimestamp(int(m.group(1)) / 1000, UTC).replace(microsecond=0) if m else None


def parse_portfolio_xlsx(content: bytes) -> dict:
    """Devuelve {'lines': [...], 'cash': Decimal|None, 'sha256': str}. Falla si el formato no coincide."""
    sha = hashlib.sha256(content).hexdigest()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:  # openpyxl lanza varias clases según el daño del archivo
        raise ImportError_("No se pudo leer el XLSX (¿archivo dañado o de otro formato?).") from exc
    ws = wb.worksheets[0]
    rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
    header_idx = next(
        (i for i, r in enumerate(rows) if r and str(r[0] or "").strip().lower() == HEADER_KEY), None
    )
    if header_idx is None:
        raise ImportError_("Formato no reconocido: no encontré el encabezado 'Emisora/Fondo'.")
    header = [str(c or "").strip().lower() for c in rows[header_idx]]
    col = {}
    for name, needles in {
        "ticker": ("emisora",), "quantity": ("t�tulos", "títulos", "titulos"),
        "avg": ("costo promedio",), "price": ("precio mercado",), "value": ("valor mercado",),
    }.items():  # fmt: skip
        col[name] = next((i for i, h in enumerate(header) if any(n in h for n in needles)), None)
    if col["quantity"] is None:  # el encabezado puede venir con caracteres dañados
        col["quantity"] = 1
    if None in (col["ticker"], col["avg"], col["price"], col["value"]):
        raise ImportError_("Formato no reconocido: faltan columnas de costo, precio o valor de mercado.")

    lines, cash = [], None
    section = "equities"
    for r in rows[header_idx + 1 :]:
        first = str(r[0] or "").strip()
        if not first:
            continue
        if first.lower() == HEADER_KEY:
            continue
        if first.lower() == "liquidez":
            section = "cash"
            continue
        if section == "cash":
            if first.lower() == "efectivo":
                cash = money(r[col["value"]])
            continue
        if r[col["quantity"]] in (None, "", "-"):
            continue
        lines.append(
            {
                "ticker": first.upper(),
                "quantity": quantity(r[col["quantity"]]),
                "average_cost": money(r[col["avg"]]),
                "market_price": money(r[col["price"]]),
                "market_value": money(r[col["value"]]),
                "raw": [None if c is None else str(c) for c in r],
            }
        )
    if not lines:
        raise ImportError_("El archivo no contiene posiciones.")
    tickers = [ln["ticker"] for ln in lines]
    if len(set(tickers)) != len(tickers):
        raise ImportError_("El archivo repite emisoras; no se puede importar sin ambigüedad.")
    return {"lines": lines, "cash": cash, "sha256": sha}
