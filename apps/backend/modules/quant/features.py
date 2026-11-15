"""Factores por instrumento y fecha, calculados SOLO con velas hasta esa fecha (sin mirar al futuro).

Son factores sencillos y de dominio público (momentum, volatilidad, distancia a medias, RSI, caída desde máximo).
Funcionan con cierres solos para que cualquier historial guardado sirva. Son una línea base, no un catálogo
validado: ningún factor se da por bueno hasta que supera la evaluación fuera de muestra."""

import math

from modules.technical.indicators import rsi

FEATURES = ["ret_5", "ret_20", "ret_60", "vol_20", "dist_sma_20", "dist_sma_50", "dd_60", "rsi_14"]
MIN_HISTORY = 61  # barras necesarias para ret_60 (cierre actual + 60 anteriores)


def _mean(xs):
    return sum(xs) / len(xs)


def features_at(closes: list[float], i: int, rsi_series=None) -> dict[str, float] | None:
    """Factores en la posición `i` usando closes[0..i]. None si no hay historial suficiente."""
    if i < MIN_HISTORY - 1:
        return None
    c = closes[i]
    window20, window50, window60 = closes[i - 19 : i + 1], closes[i - 49 : i + 1], closes[i - 59 : i + 1]
    rets = [closes[k] / closes[k - 1] - 1 for k in range(i - 19, i + 1)]
    m = _mean(rets)
    vol = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))
    r14 = rsi_series[i] if rsi_series is not None else None
    if r14 is None:
        return None
    return {
        "ret_5": c / closes[i - 5] - 1,
        "ret_20": c / closes[i - 20] - 1,
        "ret_60": c / closes[i - 60] - 1,
        "vol_20": vol,
        "dist_sma_20": c / _mean(window20) - 1,
        "dist_sma_50": c / _mean(window50) - 1,
        "dd_60": c / max(window60) - 1,
        "rsi_14": r14 / 100.0,
    }


def feature_series(closes: list[float]) -> list[dict[str, float] | None]:
    """Factores para cada posición de la serie (None donde no hay historial)."""
    r = rsi(closes, 14)
    return [features_at(closes, i, r) for i in range(len(closes))]
