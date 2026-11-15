"""Herramientas de LECTURA expuestas por MCP. Reutilizan los servicios del dominio; no calculan nada
por su cuenta. Cada respuesta incluye procedencia/antigüedad y un aviso (plan 13.5)."""

from datetime import datetime, timedelta

from django.utils import timezone

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


def _parse_day(raw: str | None, name: str) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.get_current_timezone()) if "T" not in raw else datetime.fromisoformat(raw)
    except ValueError:
        raise McpError(f"{name}: fecha ISO 8601 inválida.") from None


def get_transactions(cred, start: str | None = None, end: str | None = None, limit: int = 50) -> dict:
    if not 1 <= int(limit) <= MAX_TX:
        raise McpError(f"limit debe estar entre 1 y {MAX_TX}.")
    now = timezone.now()
    end_dt = _parse_day(end, "end") or now
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
