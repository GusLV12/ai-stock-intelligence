"""Análisis intradía con velas de 5 minutos: higiene de datos, VWAP y ruptura del rango de apertura (ORB).

Higiene (inspirada en las prácticas de la skill revisada, reimplementada aquí): solo velas del horario regular en la
cuadrícula de 5 minutos, solo velas COMPLETAS al corte, una vela por franja (la posterior gana), volumen faltante
= 0 (se informa). Una sesión solo es «usable» si no falta ninguna franja desde la apertura hasta el corte (si falta
una, la acción pudo estar suspendida o sin operaciones). Los datos gratuitos pueden retrasarse o faltar: se
informa su antigüedad. Es análisis descriptivo, no una recomendación."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

BAR_MINUTES = 5
OPEN, CLOSE = time(9, 30), time(16, 0)
DELAY_WARNING_MIN = 20


class IntradayError(ValueError):
    pass


def clean(raw: list[dict], tz_name: str, cutoff: datetime | None = None) -> dict:
    """`raw`: [{ts, open, high, low, close, volume}] con ts en segundos Unix UTC. Devuelve velas limpias de UNA sesión
    (la más reciente con datos) y las incidencias de higiene."""
    tz = ZoneInfo(tz_name)
    cutoff = cutoff or datetime.now(UTC)
    kept: dict[datetime, dict] = {}
    stats = {
        "dropped_null": 0,
        "dropped_off_grid": 0,
        "dropped_extended_hours": 0,
        "dropped_incomplete": 0,
        "volume_missing": 0,
    }
    for r in raw:
        if any(r.get(k) is None for k in ("open", "high", "low", "close")):
            stats["dropped_null"] += 1
            continue
        start = datetime.fromtimestamp(int(r["ts"]), UTC).astimezone(tz)
        if start.second != 0 or start.minute % BAR_MINUTES != 0:
            stats["dropped_off_grid"] += 1  # la cotización «en vivo» no cae en la cuadrícula
            continue
        if not (OPEN <= start.time() < CLOSE):
            stats["dropped_extended_hours"] += 1
            continue
        if start + timedelta(minutes=BAR_MINUTES) > cutoff.astimezone(tz):
            stats["dropped_incomplete"] += 1  # la vela en curso puede cambiar
            continue
        vol = r.get("volume")
        if vol is None:
            stats["volume_missing"] += 1
        kept[start] = {"start": start, "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
                       "close": float(r["close"]), "volume": float(vol or 0)}  # fmt: skip
    if not kept:
        return {"session": None, "bars": [], "usable": False, "missing_slots": [], "hygiene": stats}
    session = max(k.date() for k in kept)
    bars = sorted((b for k, b in kept.items() if k.date() == session), key=lambda b: b["start"])
    first = datetime.combine(session, OPEN, tzinfo=tz)
    last_start = bars[-1]["start"]
    expected = int((last_start - first).total_seconds() // (BAR_MINUTES * 60)) + 1
    have = {b["start"] for b in bars}
    missing = [
        first + timedelta(minutes=BAR_MINUTES * i)
        for i in range(expected)
        if first + timedelta(minutes=BAR_MINUTES * i) not in have
    ]
    return {"session": session, "bars": bars, "usable": not missing and bars[0]["start"] == first,
            "missing_slots": [m.strftime("%H:%M") for m in missing], "hygiene": stats}  # fmt: skip


def vwap(bars: list[dict]) -> list[float | None]:
    """VWAP acumulado de la sesión: Σ(precio típico × volumen) / Σ volumen, con precio típico = (H+L+C)/3.
    None mientras el volumen acumulado sea 0."""
    out, pv, vol = [], 0.0, 0.0
    for b in bars:
        pv += (b["high"] + b["low"] + b["close"]) / 3 * b["volume"]
        vol += b["volume"]
        out.append(pv / vol if vol > 0 else None)
    return out


def orb(
    bars: list[dict], or_minutes: int = 15, no_entries_after: time = time(15, 30), min_price: float = 0.0
) -> dict:
    """Ruptura del rango de apertura. Rango = máximo y mínimo REALES de los primeros `or_minutes` minutos. Ruptura
    alcista = primera vela completa posterior cuyo CIERRE supera el máximo del rango (se marca al cierre de esa vela);
    ruptura bajista, análogo bajo el mínimo. Sin entradas después de `no_entries_after`."""
    if or_minutes <= 0 or or_minutes % BAR_MINUTES:
        raise IntradayError("or_minutes debe ser un múltiplo positivo de 5.")
    n_range = or_minutes // BAR_MINUTES
    if len(bars) <= n_range:
        return {
            "status": "range_not_complete",
            "reason": f"Se necesitan más de {n_range} velas para cerrar el rango.",
            "hit": None,
        }
    rng, after = bars[:n_range], bars[n_range:]
    hi, lo = max(b["high"] for b in rng), min(b["low"] for b in rng)
    out = {
        "status": "ok", "or_minutes": or_minutes, "range_high": hi, "range_low": lo,
        "range_end": (rng[-1]["start"] + timedelta(minutes=BAR_MINUTES)).strftime("%H:%M"), "hit": None, "breakdown": None,
        "rule": f"Primera vela completa tras los primeros {or_minutes} min con cierre > máximo del rango (cierre < mínimo para la bajista).",
    }  # fmt: skip
    if rng[0]["close"] < min_price and bars[-1]["close"] < min_price:
        out["status"] = "below_min_price"
        return out
    for b in after:
        end = b["start"] + timedelta(minutes=BAR_MINUTES)
        if end.time() > no_entries_after:
            out["late"] = True
            break
        if out["hit"] is None and b["close"] > hi:
            out["hit"] = {
                "time": end.strftime("%H:%M"),
                "close": b["close"],
                "above_range_pct": (b["close"] / hi - 1) * 100,
            }
        if out["breakdown"] is None and b["close"] < lo:
            out["breakdown"] = {
                "time": end.strftime("%H:%M"),
                "close": b["close"],
                "below_range_pct": (1 - b["close"] / lo) * 100,
            }
    return out
