"""Métricas de riesgo HISTÓRICAS (plan 8.3): volatilidad, caída máxima y correlaciones con ventana explícita.

Son descriptivas del pasado reciente: no predicen. Se calculan sobre cierres diarios (sin dividendos reinvertidos).
Las series sin historial suficiente se marcan; nunca se rellenan ni se asumen. Aritmética en float: son métricas
analíticas, no asientos contables."""

import math
from datetime import date
from itertools import combinations, pairwise

TRADING_DAYS = 252
MIN_OBSERVATIONS = 30  # retornos por activo
MIN_OVERLAP = 60  # fechas comunes para correlacionar un par


def returns(bars: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Retornos simples entre cierres consecutivos de la serie."""
    out = []
    for (_, p0), (d1, p1) in pairwise(bars):
        if p0 > 0:
            out.append((d1, p1 / p0 - 1))
    return out


def _mean(xs):
    return sum(xs) / len(xs)


def _stdev(xs):
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def max_drawdown(bars: list[tuple[date, float]]) -> float:
    peak, worst = bars[0][1], 0.0
    for _, p in bars:
        peak = max(peak, p)
        worst = min(worst, p / peak - 1)
    return worst


def correlation(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b) or len(a) < 2:
        return None
    ma, mb = _mean(a), _mean(b)
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None  # una serie sin variación no tiene correlación definida
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / math.sqrt(va * vb)


def analyze(series: dict[str, list[tuple[date, float]]], weights: dict[str, float] | None = None) -> dict:
    """`series`: {ticker: [(fecha, cierre)] ordenado}. `weights`: pesos sobre el VALOR TOTAL (incluye efectivo, que
    aporta volatilidad cero)."""
    per_asset, rets, skipped = {}, {}, {}
    for t, bars in series.items():
        r = returns(bars)
        if len(r) < MIN_OBSERVATIONS:
            skipped[t] = f"Historial insuficiente ({len(r)} retornos; mínimo {MIN_OBSERVATIONS})."
            continue
        values = [x for _, x in r]
        rets[t] = dict(r)
        per_asset[t] = {
            "observations": len(r), "from": bars[0][0], "to": bars[-1][0],
            "annualized_volatility": _stdev(values) * math.sqrt(TRADING_DAYS),
            "max_drawdown": max_drawdown(bars),
            "return_over_window": bars[-1][1] / bars[0][1] - 1,
        }  # fmt: skip

    pairs, matrix = [], {t: {} for t in rets}
    for a, b in combinations(sorted(rets), 2):
        common = sorted(set(rets[a]) & set(rets[b]))
        if len(common) < MIN_OVERLAP:
            matrix[a][b] = matrix[b][a] = None
            continue
        c = correlation([rets[a][d] for d in common], [rets[b][d] for d in common])
        matrix[a][b] = matrix[b][a] = c
        if c is not None:
            pairs.append({"a": a, "b": b, "correlation": c, "overlap_days": len(common)})
    for t, row in matrix.items():
        row[t] = 1.0
    pairs.sort(key=lambda p: -p["correlation"])

    out = {
        "window": {"from": min((v["from"] for v in per_asset.values()), default=None),
                   "to": max((v["to"] for v in per_asset.values()), default=None)},
        "assets": per_asset,
        "correlation_matrix": matrix,
        "most_correlated_pairs": pairs[:5],
        "average_pairwise_correlation": _mean([p["correlation"] for p in pairs]) if pairs else None,
        "skipped": skipped,
        "portfolio": None,
        "warnings": ["Métricas históricas del pasado reciente: no son una predicción de riesgo futuro."],
    }
    if weights:
        out["portfolio"] = _portfolio(rets, per_asset, weights, out["warnings"])
    if skipped:
        out["warnings"].append("Emisoras sin historial suficiente: " + ", ".join(sorted(skipped)) + ".")
    return out


def _portfolio(rets, per_asset, weights, warnings):
    """Volatilidad del portafolio con los pesos dados sobre las fechas comunes de todas las series con historial."""
    covered = {t: w for t, w in weights.items() if t in rets and w > 0}
    total_risky = sum(w for t, w in weights.items() if t != "CASH" and w > 0)
    covered_weight = sum(covered.values())
    if len(covered) < 1:
        return None
    common = sorted(set.intersection(*(set(rets[t]) for t in covered)))
    if len(common) < MIN_OVERLAP:
        warnings.append(f"Solo {len(common)} fechas comunes entre las posiciones: no se calcula la volatilidad del portafolio.")
        return None
    daily = [sum(w * rets[t][d] for t, w in covered.items()) for d in common]  # el efectivo aporta 0
    cov_ratio = covered_weight / total_risky if total_risky else 0.0
    if cov_ratio < 0.8:
        warnings.append(f"Las posiciones con historial cubren {cov_ratio:.0%} del valor invertido: la volatilidad del portafolio es parcial.")
    weighted_avg_vol = sum(w * per_asset[t]["annualized_volatility"] for t, w in covered.items())
    vol = _stdev(daily) * math.sqrt(TRADING_DAYS)
    return {
        "annualized_volatility": vol,
        "observations": len(common),
        "covered_weight": covered_weight,
        "covered_share_of_invested": cov_ratio,
        "weighted_average_asset_volatility": weighted_avg_vol,
        "diversification_benefit": weighted_avg_vol - vol,  # >= 0: la diversificación reduce la volatilidad
    }
