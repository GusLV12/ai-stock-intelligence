"""Simulador de shocks DETERMINISTA (plan 8.5): aplica variaciones que TÚ ingresas y muestra el impacto.

No es un escenario probabilístico: no hay distribución, dependencia entre activos ni calibración. Los supuestos
se listan en cada resultado. Un shock a un grupo (p. ej. todas las acciones) supone la misma variación en todos
sus miembros (beta = 1): no estima cuánto caería cada una de verdad."""

from dataclasses import replace
from decimal import Decimal

from . import exposure

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)
MAX_SHOCKS = 50

PRESETS = {
    "stocks_minus_10": {
        "label": "Acciones −10% (aprox. de «Nasdaq −10%», supone beta 1 en todas)",
        "shocks": [{"target": "class:STOCK", "pct": -10}, {"target": "class:ADR", "pct": -10}],
    },
    "crypto_minus_20": {"label": "Cripto −20%", "shocks": [{"target": "class:CRYPTO", "pct": -20}]},
    "crypto_crash": {
        "label": "BTC −20% y ETH −25% a la vez",
        "shocks": [{"target": "BTC", "pct": -20}, {"target": "ETH", "pct": -25}],
    },
    "usd_mxn_plus_5": {"label": "USD/MXN +5% (el peso se deprecia)", "shocks": [], "fx_usd_mxn_pct": 5},
    "usd_mxn_minus_5": {"label": "USD/MXN −5% (el peso se aprecia)", "shocks": [], "fx_usd_mxn_pct": -5},
}


class ShockError(ValueError):
    pass


def _matches(h, target: str) -> bool:
    t = target.upper()
    if t == "ALL":
        return h.asset_class != "CASH"  # el efectivo en USD no cambia de valor en USD
    if t.startswith("CLASS:"):
        return h.asset_class == t[6:]
    return h.ticker.upper() == t


def validate_shocks(shocks) -> list[dict]:
    if not isinstance(shocks, list) or len(shocks) > MAX_SHOCKS:
        raise ShockError(f"shocks debe ser una lista de hasta {MAX_SHOCKS} elementos.")
    out = []
    for s in shocks:
        if not isinstance(s, dict) or not str(s.get("target", "")).strip():
            raise ShockError("Cada shock requiere target (emisora, class:CLASE o all) y pct.")
        try:
            pct = Decimal(str(s.get("pct")))
        except Exception:  # noqa: BLE001 - cualquier valor no numérico
            raise ShockError(f"pct inválido para {s.get('target')}.") from None
        if not pct.is_finite() or pct < -100 or pct > 1000:
            raise ShockError(f"pct de {s.get('target')} debe estar entre -100 y 1000.")
        out.append({"target": str(s["target"]).strip(), "pct": pct})
    return out


def simulate(
    holdings: list, shocks: list[dict], fx_pct: Decimal | None = None, fx_rate: Decimal | None = None
) -> dict:
    shocks = validate_shocks(shocks)  # siempre se valida, también cuando ya vienen como Decimal
    if fx_pct is not None:
        if fx_pct <= -100 or fx_pct > 1000:
            raise ShockError("La variación de USD/MXN debe estar entre -100% y 1000%.")
        if fx_rate is None:
            raise ShockError("No hay tipo de cambio USD/MXN de referencia para aplicar el shock cambiario.")
    if not shocks and fx_pct is None:
        raise ShockError("Indica al menos un shock o una variación de USD/MXN.")

    valued = [h for h in holdings if h.value_base is not None]
    if not valued:
        raise ShockError("No hay posiciones valoradas para simular.")
    unmatched = [s["target"] for s in shocks if not any(_matches(h, s["target"]) for h in valued)]
    rows, shocked = [], []
    for h in valued:
        factor = ONE
        applied = []
        for s in shocks:
            if _matches(h, s["target"]):
                factor *= ONE + s["pct"] / HUNDRED
                applied.append(f"{s['target']} {s['pct']:+}%")
        after = h.value_base * factor
        shocked.append(replace(h, value_base=after))
        rows.append({
            "ticker": h.ticker, "asset_class": h.asset_class, "before_usd": h.value_base, "after_usd": after,
            "change_usd": after - h.value_base, "change_pct": (factor - ONE) * HUNDRED, "applied": applied,
        })  # fmt: skip
    before = sum((r["before_usd"] for r in rows), ZERO)
    after = sum((r["after_usd"] for r in rows), ZERO)
    by_class = {}
    for r in rows:
        c = by_class.setdefault(r["asset_class"], {"before_usd": ZERO, "after_usd": ZERO})
        c["before_usd"] += r["before_usd"]
        c["after_usd"] += r["after_usd"]
    for c in by_class.values():
        c["change_usd"] = c["after_usd"] - c["before_usd"]
    assumptions = [
        "Escenario DETERMINISTA: solo aplica las variaciones que ingresaste; no es una predicción ni una probabilidad.",
        "Un shock a un grupo (class: o all) supone la misma variación en todos sus miembros (beta = 1).",
        "Los shocks sobre la misma posición se componen multiplicando (no se suman).",
        "El efectivo en USD no cambia de valor en USD.",
    ]
    if unmatched:
        assumptions.append(f"Shocks sin efecto (ninguna posición coincide): {', '.join(unmatched)}.")
    result = {
        "before_usd": before,
        "after_usd": after,
        "change_usd": after - before,
        "change_pct": (after - before) / before * HUNDRED if before else ZERO,
        "positions": sorted(rows, key=lambda r: r["change_usd"]),
        "by_asset_class": by_class,
        "post_shock_exposure": exposure.analyze(shocked),
        "assumptions": assumptions,
        "mxn_view": None,
    }
    if fx_rate is not None:
        r0 = fx_rate
        r1 = fx_rate * (ONE + (fx_pct or ZERO) / HUNDRED)
        result["mxn_view"] = {
            "rate_before": r0,
            "rate_after": r1,
            "before_mxn": before * r0,
            "after_mxn": after * r1,
            "change_mxn": after * r1 - before * r0,
            "asset_component_mxn": (after - before) * r0,  # variación de precios al tipo de cambio inicial
            "fx_component_mxn": after * (r1 - r0),  # efecto del tipo de cambio sobre el valor final
            "note": "El componente cambiario no es variación del precio del activo (plan 14.4).",
        }
    return result
