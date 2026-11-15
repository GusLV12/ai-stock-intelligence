"""Métricas de capacidad predictiva: IC (Pearson) y Rank IC (Spearman) por fecha, y su estabilidad.

El IC mide si el orden que da el modelo coincide con el orden de los retornos futuros ese día. Importa más que
acertar la dirección. Las etiquetas a `horizon` sesiones se solapan entre fechas vecinas, así que los ICs diarios no
son independientes: además del t ingenuo se reporta un t con muestra efectiva n/horizon, que es el que debe usarse."""

import math
from collections import defaultdict


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda k: values[k])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2
        i = j + 1
    return out


def pearson(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 3 or n != len(b):
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if sa == 0 or sb == 0:
        return None  # sin dispersión no hay correlación definida
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / (sa * sb)


def spearman(a: list[float], b: list[float]) -> float | None:
    return pearson(_ranks(a), _ranks(b)) if len(a) == len(b) else None


def ic_by_date(rows: list[dict], scores: list[float], min_names: int = 5) -> list[dict]:
    """IC y Rank IC por fecha entre `scores` (alineados con `rows`) y la etiqueta `y`. Omite fechas sin etiquetas suficientes."""
    groups = defaultdict(lambda: ([], []))
    for r, s in zip(rows, scores, strict=True):
        if r["y"] is not None:
            groups[r["date"]][0].append(s)
            groups[r["date"]][1].append(r["y"])
    out = []
    for d in sorted(groups):
        s, y = groups[d]
        if len(s) < min_names:
            continue
        ic, ric = pearson(s, y), spearman(s, y)
        if ic is not None and ric is not None:
            out.append({"date": d, "ic": ic, "rank_ic": ric, "n": len(s)})
    return out


def _stats(values: list[float], horizon: int) -> dict:
    n = len(values)
    if n < 2:
        return {
            "n": n,
            "mean": values[0] if values else None,
            "std": None,
            "ir": None,
            "t_naive": None,
            "t_adjusted": None,
            "pct_positive": None,
        }
    m = sum(values) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))
    n_eff = max(n / horizon, 1.0)
    return {
        "n": n, "mean": m, "std": sd, "ir": m / sd if sd else None,
        "t_naive": m / (sd / math.sqrt(n)) if sd else None,
        "t_adjusted": m / (sd / math.sqrt(n_eff)) if sd else None,
        "pct_positive": sum(1 for v in values if v > 0) / n,
    }  # fmt: skip


def summarize(ics: list[dict], horizon: int, blocks: int = 3) -> dict:
    """Resumen de IC/Rank IC y su estabilidad por bloques cronológicos."""
    res = {
        "ic": _stats([x["ic"] for x in ics], horizon),
        "rank_ic": _stats([x["rank_ic"] for x in ics], horizon),
        "n_dates": len(ics),
    }
    size = len(ics) // blocks
    chunks = (
        [ics[k * size : (k + 1) * size if k < blocks - 1 else len(ics)] for k in range(blocks)]
        if size >= 1
        else []
    )
    res["rank_ic_blocks"] = [sum(x["rank_ic"] for x in c) / len(c) for c in chunks]
    return res
