"""Indicadores técnicos (funciones puras, deterministas, sobre listas de floats).

Cada función devuelve una lista ALINEADA con la entrada: las posiciones de calentamiento son None. Un indicador
describe el pasado con una fórmula exacta; no es una predicción. Las definiciones están en cada docstring para
poder reproducirlas con las mismas velas y parámetros (plan 9.1)."""

import math

Series = list[float | None]


def sma(values: list[float], n: int) -> Series:
    if n < 1:
        raise ValueError("n debe ser >= 1")
    out: Series = [None] * len(values)
    window = 0.0
    for i, v in enumerate(values):
        window += v
        if i >= n:
            window -= values[i - n]
        if i >= n - 1:
            out[i] = window / n
    return out


def ema(values: list[float], n: int) -> Series:
    """EMA con alfa = 2/(n+1) sembrada con la SMA de los primeros n valores."""
    if n < 1:
        raise ValueError("n debe ser >= 1")
    out: Series = [None] * len(values)
    if len(values) < n:
        return out
    alpha = 2 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * alpha + prev * (1 - alpha)
        out[i] = prev
    return out


def rsi(closes: list[float], n: int = 14) -> Series:
    """RSI de Wilder: medias de ganancia y pérdida (la primera es simple; luego suavizado de Wilder).
    RSI = 100 − 100/(1+RS); 100 si no hay pérdidas y sí ganancias; 50 si el precio no se movió."""
    out: Series = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n

    def value(g, loss):
        if loss == 0:
            return 50.0 if g == 0 else 100.0
        return 100 - 100 / (1 + g / loss)

    out[n] = value(avg_g, avg_l)
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
        out[i + 1] = value(avg_g, avg_l)
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, Series]:
    """MACD = EMA(rápida) − EMA(lenta); señal = EMA(signal) de la línea MACD; histograma = MACD − señal."""
    if fast >= slow:
        raise ValueError("fast debe ser menor que slow")
    ef, es = ema(closes, fast), ema(closes, slow)
    line: Series = [None if a is None or b is None else a - b for a, b in zip(ef, es, strict=True)]
    first = next((i for i, v in enumerate(line) if v is not None), None)
    sig: Series = [None] * len(closes)
    if first is not None:
        tail_sig = ema([v for v in line[first:] if v is not None], signal)
        for j, v in enumerate(tail_sig):
            sig[first + j] = v
    hist: Series = [None if a is None or b is None else a - b for a, b in zip(line, sig, strict=True)]
    return {"macd": line, "signal": sig, "histogram": hist}


def true_range(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    return tr


def atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> Series:
    """ATR de Wilder: media simple de los primeros n rangos verdaderos y luego suavizado de Wilder."""
    out: Series = [None] * len(closes)
    if len(closes) < n:
        return out
    tr = true_range(highs, lows, closes)
    prev = sum(tr[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(tr)):
        prev = (prev * (n - 1) + tr[i]) / n
        out[i] = prev
    return out


def bollinger(closes: list[float], n: int = 20, k: float = 2.0) -> dict[str, Series]:
    """Bandas de Bollinger: media simple ± k desviaciones estándar POBLACIONALES (como TradingView)."""
    mid = sma(closes, n)
    upper: Series = [None] * len(closes)
    lower: Series = [None] * len(closes)
    for i, m in enumerate(mid):
        if m is None:
            continue
        window = closes[i - n + 1 : i + 1]
        sd = math.sqrt(sum((x - m) ** 2 for x in window) / n)
        upper[i], lower[i] = m + k * sd, m - k * sd
    return {"middle": mid, "upper": upper, "lower": lower}


def relative_volume(volumes: list[float | None], n: int = 20) -> Series:
    """RVOL = volumen / media del volumen de los n días ANTERIORES (sin incluir el actual)."""
    out: Series = [None] * len(volumes)
    for i in range(n, len(volumes)):
        prev = volumes[i - n : i]
        if volumes[i] is None or any(v is None for v in prev):
            continue
        avg = sum(prev) / n
        out[i] = volumes[i] / avg if avg > 0 else None
    return out


def key_levels(
    highs: list[float], lows: list[float], last_close: float, wing: int = 5, tolerance: float = 0.01
) -> list[dict]:
    """Soportes y resistencias por pivotes: un máximo (mínimo) es pivote si supera (queda por debajo de) los
    `wing` días a cada lado. Los pivotes a menos de `tolerance` (1%) entre sí forman un nivel con su número de
    toques. Un nivel es soporte si está bajo el último cierre y resistencia si está sobre él."""
    pivots = []
    for i in range(wing, len(highs) - wing):
        if highs[i] == max(highs[i - wing : i + wing + 1]):
            pivots.append((i, highs[i]))
        if lows[i] == min(lows[i - wing : i + wing + 1]):
            pivots.append((i, lows[i]))
    clusters: list[dict] = []
    for idx, price in sorted(pivots, key=lambda p: p[1]):
        if clusters and abs(price - clusters[-1]["price"]) / clusters[-1]["price"] <= tolerance:
            c = clusters[-1]
            c["touches"] += 1
            c["price"] = (c["price"] * (c["touches"] - 1) + price) / c["touches"]
            c["last_index"] = max(c["last_index"], idx)
        else:
            clusters.append({"price": price, "touches": 1, "last_index": idx})
    for c in clusters:
        c["kind"] = "support" if c["price"] < last_close else "resistance"
    return sorted(clusters, key=lambda c: abs(c["price"] - last_close))
