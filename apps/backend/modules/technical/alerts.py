"""Evaluación de alertas. Idempotente: una alerta disparada no se vuelve a evaluar."""

from django.utils import timezone

from modules.pricing.services import latest_price

from .api import _bars_for
from .models import PriceAlert
from .signals import SignalError, analyze

MAX_ACTIVE_PER_USER = 100
SIGNAL_IDS = {
    "breakout",
    "reclaim_ma",
    "oversold",
    "oversold_bounce",
    "trend_up",
    "macd_bull_cross",
    "volume_spike",
    "near_level",
}


def _bars(instrument_id):
    return _bars_for(instrument_id, 300)[0]


def _check(alert: PriceAlert) -> dict | None:
    """Devuelve el detalle del disparo o None si todavía no se cumple (o no se puede evaluar)."""
    now = timezone.now()
    if alert.kind in (PriceAlert.Kind.PRICE_ABOVE, PriceAlert.Kind.PRICE_BELOW):
        mp = latest_price(alert.instrument)
        if mp is None:
            return None
        hit = (
            mp.price >= alert.threshold
            if alert.kind == PriceAlert.Kind.PRICE_ABOVE
            else mp.price <= alert.threshold
        )
        if not hit:
            return None
        age = round((now - mp.observed_at).total_seconds() / 3600, 1)
        return {"price": format(mp.price.normalize(), "f"), "provider": mp.provider, "observed_at": mp.observed_at.isoformat(),
                "age_hours": age, "threshold": format(alert.threshold.normalize(), "f"), "stale": age > 72}  # fmt: skip
    bars = _bars(alert.instrument_id)
    if len(bars) < 2:
        return None
    try:
        res = analyze(bars)
    except SignalError:
        return None
    sig = next((s for s in res["signals"] if s["id"] == alert.signal_id), None)
    if sig is None or sig["fired"] is not True:
        return None
    return {
        "signal": alert.signal_id,
        "rule": sig["rule"],
        "as_of": str(res["as_of"]),
        "conditions": sig["conditions"],
    }


def evaluate(alerts) -> list[PriceAlert]:
    """Evalúa las alertas activas dadas y marca las que se cumplen. Devuelve las disparadas."""
    fired = []
    for a in alerts:
        if not a.active or a.triggered_at is not None:
            continue
        detail = _check(a)
        if detail is None:
            continue
        a.triggered_at, a.triggered_detail, a.active = timezone.now(), detail, False
        a.save(update_fields=["triggered_at", "triggered_detail", "active"])
        fired.append(a)
    return fired
