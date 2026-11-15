"""Herramientas de LECTURA expuestas por MCP. Reutilizan los servicios del dominio; no calculan nada
por su cuenta. Cada respuesta incluye procedencia/antigüedad y un aviso (plan 13.5)."""

from datetime import datetime, timedelta

from django.utils import timezone

from modules.calculators.engine import CalcError
from modules.calculators.service import calculate, target_buy_price
from modules.fundamentals.screener import ScreenError, run_screen
from modules.intelligence.risk_api import risk_report
from modules.intelligence.shocks import ShockError
from modules.intelligence.shocks_api import scenario_report
from modules.portfolio.dashboard import build_dashboard
from modules.portfolio.models import Transaction
from modules.technical.api import _bars_for, _find_instrument, _freshness
from modules.technical.signals import SignalError, analyze
from modules.valuations.fair_value import plain

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
        raise McpError(
            str(exc) if isinstance(exc, CalcError) else "Parámetros con formato inválido."
        ) from None


def calculate_target_buy_price(cred, params: dict) -> dict:
    try:
        return target_buy_price(params)
    except CALC_ERRORS as exc:
        raise McpError(
            str(exc) if isinstance(exc, CalcError) else "Parámetros con formato inválido."
        ) from None


# ------------------------------------------------------------------------- análisis (solo lectura, sin efectos)
def portfolio_risk(cred, window_days: int = 365) -> dict:
    """Volatilidad, caída máxima y correlaciones históricas de las cuentas autorizadas."""
    try:
        window = int(window_days)
    except (TypeError, ValueError):
        raise McpError("window_days debe ser un entero.") from None
    try:
        out = risk_report(cred.owner, window, account_ids=allowed_account_ids(cred))
    except ValueError as exc:
        raise McpError(str(exc)) from None
    return {**out, "disclaimer_mcp": DISCLAIMER}


def simulate_portfolio(cred, scenario: dict) -> dict:
    """Shocks deterministas (no es una predicción) sobre las cuentas autorizadas. No modifica nada."""
    if not isinstance(scenario, dict):
        raise McpError("scenario debe ser un objeto con preset y/o shocks.")
    try:
        out = scenario_report(cred.owner, scenario, account_ids=allowed_account_ids(cred))
    except ShockError as exc:
        raise McpError(str(exc)) from None
    return {
        **out,
        "disclaimer_mcp": "Escenario hipotético con tus supuestos; no es una predicción ni asesoría de inversión.",
    }


def screen_undervalued_stocks(cred, params: dict | None = None) -> dict:
    """Screener por reglas explícitas sobre fundamentales cargados. NO guarda la corrida (solo lectura)."""
    try:
        res = run_screen(cred.owner, dict(params or {}))
    except ScreenError as exc:
        raise McpError(str(exc)) from None
    except (TypeError, ValueError, KeyError, AttributeError):
        raise McpError("Filtros con formato inválido.") from None
    res["rows"] = res.get("rows", res.get("results", []))[:50]  # acota el tamaño de la respuesta
    return {
        **plain(res),
        "disclaimer_mcp": "Filtro por reglas sobre datos cargados; no es una recomendación de compra.",
    }


def technical_analysis(cred, ticker: str, days: int = 300) -> dict:
    """Indicadores y señales auditables (con su regla y valores) de una emisora con historial guardado."""
    ticker = str(ticker or "").strip().upper()
    if not ticker or len(ticker) > 20:
        raise McpError("Indica un ticker.")
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise McpError("days debe ser un entero.") from None
    if not 30 <= days <= 1000:
        raise McpError("days debe estar entre 30 y 1000.")
    ins = _find_instrument(ticker)
    if ins is None:
        raise McpError("Emisora no encontrada.")
    bars, provider = _bars_for(ins.id, days)
    if len(bars) < 2:
        return {
            "ticker": ticker,
            "evaluable": False,
            "note": "Sin historial guardado: ejecuta refresh_price_history.",
        }
    try:
        res = analyze(bars)
    except SignalError as exc:
        raise McpError(str(exc)) from None
    return plain({
        "ticker": ticker, "evaluable": True, "as_of": res["as_of"], "freshness": _freshness(bars, provider),
        "context": res["context"], "signals": res["signals"], "plan_suggestion": res["plan_suggestion"], "note": res["note"],
        "disclaimer_mcp": "Indicadores y eventos reproducibles sobre cierres diarios con retraso; no son predicciones ni recomendaciones.",
    })  # fmt: skip
