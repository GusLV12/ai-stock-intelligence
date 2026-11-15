"""Herramientas de LECTURA expuestas por MCP. Reutilizan los servicios del dominio; no calculan nada
por su cuenta. Cada respuesta incluye procedencia/antigüedad y un aviso (plan 13.5)."""

from datetime import datetime, timedelta

from django.utils import timezone

from modules.calculators.engine import CalcError
from modules.calculators.service import calculate, target_buy_price
from modules.portfolio.dashboard import build_dashboard
from modules.portfolio.models import Transaction

from .services import McpError, allowed_account_ids

MAX_TX = 200
MAX_SPAN_DAYS = 366
DISCLAIMER = (
    "Datos contables del usuario con precios de reportes u observaciones (no tiempo real). "
    "No es asesoría de inversión."
)


def portfolio_summary(cred) -> dict:
    d = build_dashboard(cred.owner, account_ids=allowed_account_ids(cred))
    return {
        "totals_by_currency": d["totals_by_currency"],
        "fx": d["fx"],
        "performance": d["performance"],
        "prices": d["prices"],
        "unpriced_positions": d["unpriced_positions"],
        "position_count": len(d["positions"]),
        "disclaimer": DISCLAIMER,
    }


def list_positions(cred) -> dict:
    d = build_dashboard(cred.owner, account_ids=allowed_account_ids(cred))
    return {"positions": d["positions"], "prices": d["prices"], "disclaimer": DISCLAIMER}


def _parse_day(raw: str | None, name: str, end_of_day: bool = False) -> datetime | None:
    """Fecha ISO 8601. Sin hora se interpreta en hora de México; un `end` sin hora incluye todo ese día."""
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise McpError(f"{name}: fecha ISO 8601 inválida.") from None
    if "T" in raw or " " in raw.strip():
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.get_current_timezone())
        return parsed
    day = parsed.replace(tzinfo=timezone.get_current_timezone())
    return day + timedelta(days=1) - timedelta(microseconds=1) if end_of_day else day


def get_transactions(cred, start: str | None = None, end: str | None = None, limit: int = 50) -> dict:
    if not 1 <= int(limit) <= MAX_TX:
        raise McpError(f"limit debe estar entre 1 y {MAX_TX}.")
    now = timezone.now()
    end_dt = _parse_day(end, "end", end_of_day=True) or now
    start_dt = _parse_day(start, "start") or (end_dt - timedelta(days=90))
    if start_dt > end_dt:
        raise McpError("start no puede ser posterior a end.")
    if (end_dt - start_dt).days > MAX_SPAN_DAYS:
        raise McpError(f"El periodo no puede exceder {MAX_SPAN_DAYS} días.")
    qs = (
        Transaction.objects.filter(
            account_id__in=allowed_account_ids(cred),
            status=Transaction.Status.CONFIRMED,
            executed_at__gte=start_dt,
            executed_at__lte=end_dt,
        )
        .select_related("instrument", "account")
        .order_by("-executed_at", "-id")[: int(limit)]
    )
    return {
        "period": {"start": start_dt.isoformat(), "end": end_dt.isoformat()},
        "transactions": [
            {
                "date": t.executed_at.isoformat(),
                "account": t.account.name,
                "instrument": t.instrument.name,
                "operation": t.operation,
                "quantity": format(t.quantity.normalize(), "f"),
                "price": format(t.price_per_unit.normalize(), "f"),
                "fees": format(t.fees.normalize(), "f"),
                "currency": t.currency,
                "origin": t.origin,
            }
            for t in qs
        ],
        "disclaimer": DISCLAIMER,
    }


CALC_ERRORS = (CalcError, TypeError, ValueError, KeyError, AttributeError)


def calculate_cagr(cred, mode: str, params: dict) -> dict:
    """Calculadoras de rendimiento. Solo cálculo sobre los parámetros dados: no lee la cartera."""
    try:
        return calculate(mode, params)
    except CALC_ERRORS as exc:
        raise McpError(str(exc) if isinstance(exc, CalcError) else "Parámetros con formato inválido.") from None


def calculate_target_buy_price(cred, params: dict) -> dict:
    try:
        return target_buy_price(params)
    except CALC_ERRORS as exc:
        raise McpError(str(exc) if isinstance(exc, CalcError) else "Parámetros con formato inválido.") from None
