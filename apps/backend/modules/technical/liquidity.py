"""Liquidez a partir de velas diarias OHLCV: volumen negociado, tamaño de una posición frente al volumen, spread ESTIMADO
e impacto de orden aproximado. Funciones puras (sin base de datos ni red).

Límites que esta capa declara (no los esconde):
* Los datos son cierres diarios de un proveedor gratuito: no hay libro de órdenes ni bid/ask. El spread NO se mide, se estima con
  el método de Corwin-Schultz (2012) desde máximos y mínimos; es ruidoso y solo sirve como orden de magnitud.
* El impacto usa la regla de la raíz cuadrada con coeficiente 1: es una heurística de orden de magnitud, no una predicción de
  lo que costaría ejecutar.
* Con volumen faltante o en cero en la ventana, el resultado es "no evaluable" en lugar de rellenar huecos.
"""

from __future__ import annotations

import math
from statistics import mean, median

MIN_BARS = 20
SPREAD_WINDOW = 120  # el spread se estima en una ventana más larga que la del volumen: con pocos días un episodio volátil lo infla
SPREAD_FLOOR_BPS = 10  # por debajo, el método no distingue el spread de cero
DEFAULT_WINDOW = 20
DEFAULT_PARTICIPATION = 0.10  # fracción del volumen diario que se acepta consumir al vender/comprar
# Umbrales explícitos de volumen negociado diario en dólares (media de la ventana).
TIERS = ((50_000_000, "alta"), (5_000_000, "media"), (500_000, "baja"))
LOWEST_TIER = "muy baja"
SIZE_NOTICEABLE = 0.01  # la posición es ≥ 1 % del volumen diario: ya se nota
SIZE_HIGH = 0.10  # ≥ 10 %: puede tardar días en ejecutarse sin mover el precio

_K = 3 - 2 * math.sqrt(2)


class LiquidityError(ValueError):
    pass


def tier(dollar_adv: float) -> str:
    for floor, name in TIERS:
        if dollar_adv >= floor:
            return name
    return LOWEST_TIER


def corwin_schultz_bps(highs: list[float | None], lows: list[float | None]) -> dict:
    """Spread relativo estimado (puntos base) con el estimador de Corwin-Schultz: media de las estimaciones por pares de días.

    Se promedian las estimaciones SIN truncar (truncar cada una a cero sesga al alza cuando el spread real es pequeño) y solo el
    resultado final se recorta a cero. Es una ESTIMACIÓN ruidosa.
    """
    estimates = []
    for i in range(len(highs) - 1):
        h1, l1, h2, l2 = highs[i], lows[i], highs[i + 1], lows[i + 1]
        if None in (h1, l1, h2, l2) or min(h1, l1, h2, l2) <= 0 or h1 < l1 or h2 < l2:
            continue
        beta = math.log(h1 / l1) ** 2 + math.log(h2 / l2) ** 2
        gamma = math.log(max(h1, h2) / min(l1, l2)) ** 2
        alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / _K - math.sqrt(gamma / _K)
        estimates.append(2 * (math.exp(alpha) - 1) / (1 + math.exp(alpha)))
    if len(estimates) < 30:
        return {"bps": None, "se_bps": None, "pairs": len(estimates), "resolvable": False,
                "note": "Muy pocos pares de días con máximo y mínimo válidos (se piden 30)."}  # fmt: skip
    m = mean(estimates)
    se = math.sqrt(sum((e - m) ** 2 for e in estimates) / (len(estimates) - 1)) / math.sqrt(len(estimates))
    bps = round(max(0.0, m) * 10_000, 2)
    resolvable = bps >= SPREAD_FLOOR_BPS and m > 2 * se
    return {
        "bps": bps,
        "se_bps": round(se * 10_000, 2),
        "pairs": len(estimates),
        "resolvable": resolvable,
        "note": None if resolvable else (
            f"No distinguible de cero: este método no resuelve spreads menores a ~{SPREAD_FLOOR_BPS} pb ni con error estándar alto. "
            "Para emisoras muy líquidas el spread real suele ser de pocos puntos base."
        ),
    }  # fmt: skip


def amihud(closes: list[float], volumes: list[float | None]) -> float | None:
    """Iliquidez de Amihud: media de |rendimiento| / volumen en dólares, × 1e9 (mayor = menos líquido)."""
    vals = []
    for i in range(1, len(closes)):
        v = volumes[i]
        if v is None or v <= 0 or closes[i - 1] <= 0:
            continue
        vals.append(abs(closes[i] / closes[i - 1] - 1) / (v * closes[i]))
    return round(mean(vals) * 1e9, 4) if len(vals) >= 10 else None


def _daily_vol(closes: list[float]) -> float | None:
    rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]
    if len(rets) < 10:
        return None
    m = mean(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))


def analyze(bars: list[dict], window: int = DEFAULT_WINDOW, quantity: float | None = None,
            participation: float = DEFAULT_PARTICIPATION) -> dict:  # fmt: skip
    """`bars`: velas ascendentes con open/high/low/close/volume. `quantity`: títulos de una posición u orden hipotética."""
    if not MIN_BARS <= window <= 250:
        raise LiquidityError(f"La ventana debe estar entre {MIN_BARS} y 250 días.")
    if not 0 < participation <= 0.5:
        raise LiquidityError("La participación debe estar entre 0 y 0.5.")
    if quantity is not None and (not math.isfinite(quantity) or quantity < 0):
        raise LiquidityError("La cantidad debe ser un número no negativo.")
    sample = bars[-window:]
    spread_bars = bars[-max(window, SPREAD_WINDOW) :]
    if len(sample) < window:
        return {"evaluable": False, "reason": f"Se necesitan {window} velas y hay {len(sample)}."}
    vols = [b["volume"] for b in sample]
    missing = sum(1 for v in vols if v is None or v <= 0)
    if missing > window * 0.1:
        return {
            "evaluable": False,
            "reason": f"Faltan datos de volumen en {missing} de {window} días (máximo tolerado: 10 %). No se rellenan huecos.",
        }
    valid = [float(v) for v in vols if v is not None and v > 0]
    closes = [b["close"] for b in sample]
    adv = mean(valid)
    dollar = [float(b["volume"]) * b["close"] for b in sample if b["volume"] and b["volume"] > 0]
    dollar_adv = mean(dollar)
    last_close = closes[-1]
    out = {
        "evaluable": True,
        "window_days": window,
        "adv_shares": round(adv, 0),
        "median_volume_shares": round(median(valid), 0),
        "dollar_adv": round(dollar_adv, 0),
        "tier": tier(dollar_adv),
        "tier_thresholds_usd": {name: floor for floor, name in TIERS},
        "spread_estimate": {
            **corwin_schultz_bps([b["high"] for b in spread_bars], [b["low"] for b in spread_bars]),
            "window_days": len(spread_bars),
        },
        "amihud_x1e9": amihud(closes, vols),
        "last_close": last_close,
        "caveats": [
            "Cierres diarios de un proveedor gratuito; no hay libro de órdenes: el spread es una estimación, no una medición.",
            "El impacto usa la regla de la raíz cuadrada con coeficiente 1: orden de magnitud, no una predicción de costo.",
        ],
    }
    if quantity is not None:
        share = quantity / adv if adv > 0 else None
        out["position"] = {
            "quantity": quantity,
            "notional_usd": round(quantity * last_close, 2),
            "share_of_adv": None if share is None else round(share, 6),
            "days_to_trade_at_participation": None if share is None else round(share / participation, 2),
            "participation_assumed": participation,
            "size_flag": None if share is None else ("alto" if share >= SIZE_HIGH else "visible" if share >= SIZE_NOTICEABLE else "bajo"),
            "impact_sqrt_bps": None,
        }  # fmt: skip
        sigma = _daily_vol(closes)
        if sigma is not None and share is not None:
            out["position"]["impact_sqrt_bps"] = round(sigma * math.sqrt(min(share, 1.0)) * 10_000, 2)
            out["position"]["daily_volatility"] = round(sigma, 5)
    return out
