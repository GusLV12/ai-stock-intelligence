"""Lectura conservadora de ACTIVITY de DriveWealth; nunca ejecuta movimientos."""

import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from pypdf import PdfReader

from .services import ImportError_

DATE = r"\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})"
NUMBER = r"\(?-?\$?[\d,]+(?:\.\d+)?\)?"
ROW = re.compile(
    rf"^\s*(?P<trade>{DATE})\s+(?P<settle>{DATE})\s+(?P<currency>[A-Z]{{3}})\s+"
    rf"(?P<operation>BUY|SELL|DIV|DIVNRA)\s+(?P<ticker>[A-Z][A-Z0-9.\-]{{0,19}})\s+.+?\s+"
    rf"(?P<quantity>{NUMBER})\s+(?P<price>{NUMBER})\s+(?P<amount>{NUMBER})\s*$"
)
START_ROW = re.compile(rf"^\s*{DATE}\s+{DATE}\s+")
CONFIRMATION_ROW = re.compile(
    rf"^\s*(?P<ticker>[A-Z][A-Z0-9.\-]{{0,19}})\s{{2,}}.+?\s{{2,}}"
    rf"[A-Z]\s{{2,}}(?P<operation>Buy|Sell)\s{{2,}}(?P<time>\S+(?:\s+[AP]M)?)\s{{2,}}"
    rf"(?P<quantity>{NUMBER})\s{{2,}}(?P<price>{NUMBER})\s{{2,}}"
    rf"(?P<trade>{DATE})\s{{2,}}(?P<settle>{DATE})\s{{2,}}\S+(?:\s+\S+)*\s*$",
    re.IGNORECASE,
)
CONFIRMATION_FEE = re.compile(
    rf"^\s*(Principal Amount|Commission|Transaction Fee|Other Fees / Credits|Net Amount)\s+({NUMBER})\s*$",
    re.IGNORECASE,
)


def _date(value):
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError("Fecha no reconocida")


def _number(value):
    negative = value.startswith(("(", "-"))
    value = value.strip("()$").replace(",", "").lstrip("-")
    number = Decimal(value)
    return -number if negative else number


def parse_activity_text(pages):
    rows, warnings = [], []
    activity, found = False, False
    for page_number, text in enumerate(pages, 1):
        for number, line in enumerate(text.splitlines(), 1):
            heading = line.strip().upper()
            if heading == "ACTIVITY":
                activity, found = True, True
                continue
            if heading.endswith("ACTIVITY") and heading != "ACTIVITY":
                activity = False
                continue
            if not activity or not START_ROW.match(line):
                continue
            match = ROW.match(line)
            if not match:
                warnings.append(
                    {"page": page_number, "line": number, "reason": "Fila de actividad no reconocida"}
                )
                continue
            try:
                quantity = _number(match["quantity"])
                price = _number(match["price"])
                amount = _number(match["amount"])
                # DriveWealth puede expresar las salidas con signo negativo; una compra negativa
                # es ambigua y no debe convertirse silenciosamente en una posición positiva.
                if match["operation"] == "BUY" and quantity < 0:
                    raise ValueError("Cantidad negativa en compra")
                if match["operation"] == "SELL" and quantity < 0:
                    quantity = -quantity
                if (
                    not quantity.is_finite()
                    or not price.is_finite()
                    or not amount.is_finite()
                    or (match["operation"] in ("BUY", "SELL") and quantity <= 0)
                    or (match["operation"] in ("DIV", "DIVNRA") and quantity < 0)
                    or price < 0
                ):
                    raise ValueError("Valores inválidos")
                rows.append(
                    {
                        "page": page_number,
                        "line": number,
                        "trade_date": _date(match["trade"]),
                        "settle_date": _date(match["settle"]),
                        "currency": match["currency"],
                        "operation": match["operation"],
                        "ticker": match["ticker"],
                        "quantity": str(quantity),
                        "price": str(price),
                        "amount": str(amount),
                        "fees": None,
                        "fee_estimate": str(
                            (quantity * price * Decimal("0.0025")).quantize(Decimal("0.0001"))
                        )
                        if match["operation"] in ("BUY", "SELL")
                        else None,
                    }
                )
            except (ValueError, InvalidOperation):
                warnings.append(
                    {"page": page_number, "line": number, "reason": "Datos de operación ilegibles"}
                )
    if not found:
        raise ImportError_("No se encontró la sección ACTIVITY de DriveWealth.")
    return {"rows": rows, "warnings": warnings}


def parse_confirmation_text(pages):
    rows, warnings = [], []
    current = None
    found = False
    fee_names = {
        "principal amount": "principal_amount",
        "commission": "commission",
        "transaction fee": "transaction_fee",
        "other fees / credits": "other_fees_credits",
        "net amount": "net_amount",
    }
    for page_number, text in enumerate(pages, 1):
        for number, line in enumerate(text.splitlines(), 1):
            if "Symbol" in line and "Execution Time" in line and "Quantity" in line:
                found = True
                continue
            match = CONFIRMATION_ROW.match(line)
            if match:
                try:
                    quantity = _number(match["quantity"])
                    price = _number(match["price"])
                    if not quantity.is_finite() or quantity <= 0 or not price.is_finite() or price < 0:
                        raise ValueError("Cantidad o precio inválido")
                    current = {
                        "page": page_number,
                        "line": number,
                        "trade_date": _date(match["trade"]),
                        "settle_date": _date(match["settle"]),
                        "execution_time": match["time"],
                        "currency": "USD",
                        "currency_inferred": True,
                        "operation": match["operation"].upper(),
                        "ticker": match["ticker"].upper(),
                        "quantity": str(quantity),
                        "price": str(price),
                        "commission": None,
                        "transaction_fee": None,
                        "other_fees_credits": None,
                        "net_amount": None,
                    }
                    rows.append(current)
                except (ValueError, InvalidOperation):
                    current = None
                    warnings.append({"page": page_number, "line": number, "reason": "Operación ilegible"})
                continue
            if re.search(r"\s{2,}(?:Buy|Sell)\s{2,}", line, re.IGNORECASE):
                current = None
                warnings.append(
                    {"page": page_number, "line": number, "reason": "Operación de confirmación no reconocida"}
                )
                continue
            fee = CONFIRMATION_FEE.match(line)
            if fee and current is not None:
                current[fee_names[fee[1].lower()]] = str(_number(fee[2]))
    if not found:
        raise ImportError_("No se encontró una confirmación de operaciones compatible.")
    return {"rows": rows, "warnings": warnings}


def preview_drivewealth(content):
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise ImportError_("El PDF está protegido; usa una copia sin contraseña.")
        if len(reader.pages) > 100:
            raise ImportError_("El PDF excede el límite de 100 páginas.")
        pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    except ImportError_:
        raise
    except Exception as exc:
        raise ImportError_("No se pudo leer el PDF de DriveWealth.") from exc
    if any("Execution Time" in page and "Symbol" in page for page in pages):
        return {"document_type": "confirmation", **parse_confirmation_text(pages)}
    return {"document_type": "statement", **parse_activity_text(pages)}
