"""Exposición y concentración del portafolio (plan 8.3). Función pura sobre las posiciones valoradas."""

from collections import defaultdict
from decimal import Decimal

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)

OBJECTIVES = ("growth", "dividends", "mixed")
ETF_NOTE = (
    "Look-through de ETFs no disponible: requiere los componentes (holdings) actualizados de cada emisora, "
    "que pueden requerir licencia. Los ETFs se muestran como exposición opaca."
)


def _weights(rows, key):
    total = sum((r.value_base for r in rows), ZERO)
    acc = defaultdict(lambda: ZERO)
    for r in rows:
        acc[key(r)] += r.value_base
    # Con total 0 (p. ej. todo liquidado, solo efectivo) los pesos son 0, no una división indefinida.
    return {
        k: {"value": v, "weight": v / total if total else ZERO}
        for k, v in sorted(acc.items(), key=lambda kv: -kv[1])
    }


def analyze(holdings: list, policy: dict | None = None) -> dict:
    """`holdings`: objetos Holding. Solo cuentan los valorados y convertibles a USD; el resto se informa."""
    valued = [h for h in holdings if h.value_base is not None]
    excluded = [h.ticker for h in holdings if h.value_base is None]
    total = sum((h.value_base for h in valued), ZERO)
    if total <= 0:
        return {"total_value_usd": ZERO, "evaluable": False, "excluded": excluded,
                "note": "Sin posiciones valoradas: no hay exposición que calcular."}  # fmt: skip

    positions = sorted(valued, key=lambda h: -h.value_base)
    pos_weights = [{"ticker": h.ticker, "asset_class": h.asset_class, "value": h.value_base, "weight": h.value_base / total}
                   for h in positions]  # fmt: skip
    ws = [p["weight"] for p in pos_weights]
    hhi = sum((w * w for w in ws), ZERO)
    risky = [p for p in pos_weights if p["asset_class"] != "CASH"]
    out = {
        "evaluable": True,
        "total_value_usd": total,
        "positions": pos_weights,
        "by_asset_class": _weights(valued, lambda h: h.asset_class),
        "by_sector_kind": _weights([h for h in valued if h.asset_class != "CASH"], lambda h: h.sector_kind),
        "by_currency": _weights(valued, lambda h: h.currency),
        "by_broker": _weights(valued, lambda h: h.broker),
        "concentration": {
            "hhi": hhi,
            "effective_positions": ONE / hhi,
            "largest": {"ticker": pos_weights[0]["ticker"], "weight": ws[0]},
            "top3_weight": sum(ws[:3], ZERO),
            "top5_weight": sum(ws[:5], ZERO),
            "position_count": len(risky),
        },
        "etf_look_through": {
            "available": False,
            "note": ETF_NOTE,
            "etf_positions": [p["ticker"] for p in pos_weights if p["asset_class"] == "ETF"],
        },
        "excluded": excluded,
        "reconciliation": {"sum_of_weights": sum(ws, ZERO), "total_value_usd": total},
        "breaches": [],
    }
    if excluded:
        out["warnings"] = [f"Sin precio o sin conversión a USD, fuera de los pesos: {', '.join(excluded)}."]
    if policy:
        out["breaches"] = check_policy(pos_weights, out["by_asset_class"], policy)
        out["policy"] = {k: policy.get(k) for k in ("name", "objective", "max_position_pct", "max_class_pct")}
    return out


def check_policy(pos_weights, by_class, policy: dict) -> list[dict]:
    """Límites de la política (porcentajes 0–100). Solo se reportan incumplimientos; no se ordena nada."""
    breaches = []
    max_pos = policy.get("max_position_pct")
    if max_pos is not None:
        for p in pos_weights:
            if p["asset_class"] != "CASH" and p["weight"] * HUNDRED > max_pos:
                breaches.append({"type": "position", "subject": p["ticker"], "weight_pct": p["weight"] * HUNDRED,
                                 "limit_pct": max_pos, "excess_pct": p["weight"] * HUNDRED - max_pos})  # fmt: skip
    for cls, limit in (policy.get("max_class_pct") or {}).items():
        w = by_class.get(cls, {}).get("weight", ZERO) * HUNDRED
        if w > limit:
            breaches.append(
                {
                    "type": "asset_class",
                    "subject": cls,
                    "weight_pct": w,
                    "limit_pct": limit,
                    "excess_pct": w - limit,
                }
            )
    min_cash = policy.get("min_cash_pct")
    if min_cash is not None:
        cash = by_class.get("CASH", {}).get("weight", ZERO) * HUNDRED
        if cash < min_cash:
            breaches.append(
                {
                    "type": "min_cash",
                    "subject": "CASH",
                    "weight_pct": cash,
                    "limit_pct": min_cash,
                    "excess_pct": min_cash - cash,
                }
            )
    return breaches
