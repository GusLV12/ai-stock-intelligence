"""Panel (fecha × instrumento) con factores normalizados por fecha y etiqueta de retorno futuro.

Reglas contra el sesgo de anticipación:
- Los factores de la fecha t usan solo cierres hasta t (ver features.py).
- La señal se genera al cierre de t; se ejecuta al cierre de t+`delay` (por defecto 1 sesión después) y se mide
  hasta t+`delay`+`horizon`. Etiqueta: close[t+delay+h] / close[t+delay] − 1. Nada de la etiqueta entra en los factores.
- Los factores se vuelven rangos dentro de cada fecha (−0.5…0.5): el modelo compara emisoras entre sí ese día,
  no aprende el nivel general del mercado.
Limitación declarada: el universo es el de instrumentos con historial guardado hoy (sesgo de supervivencia)."""

import hashlib
import json
from collections import defaultdict

from .features import FEATURES, feature_series

DEFAULT_DELAY = 1


class DatasetError(ValueError):
    pass


def _rank_norm(values: list[float]) -> list[float]:
    """Rango promedio (empates) escalado a [−0.5, 0.5]."""
    n = len(values)
    order = sorted(range(n), key=lambda k: values[k])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return [r / (n - 1) - 0.5 for r in ranks] if n > 1 else [0.0] * n


def build_panel(
    series: dict[int, list[dict]], horizon: int, delay: int = DEFAULT_DELAY, min_names: int = 5
) -> dict:
    """`series`: {instrument_id: velas ascendentes con 'date' y 'close'}. Devuelve filas, fechas y descartes."""
    if horizon < 1 or delay < 0:
        raise DatasetError("horizon debe ser ≥ 1 y delay ≥ 0.")
    by_date: dict = defaultdict(list)
    for inst, bars in series.items():
        closes = [float(b["close"]) for b in bars]
        feats = feature_series(closes)
        for i, f in enumerate(feats):
            if f is None:
                continue
            j0, j1 = i + delay, i + delay + horizon
            y = closes[j1] / closes[j0] - 1 if j1 < len(closes) else None
            by_date[bars[i]["date"]].append({"inst": inst, "raw": f, "y": y})
    rows, dropped_dates = [], []
    for d in sorted(by_date):
        group = by_date[d]
        if len(group) < min_names:
            dropped_dates.append(d)
            continue
        norm = {k: _rank_norm([g["raw"][k] for g in group]) for k in FEATURES}
        labelled = [g["y"] for g in group if g["y"] is not None]
        for idx, g in enumerate(group):
            rows.append({"date": d, "inst": g["inst"], "x": {k: norm[k][idx] for k in FEATURES}, "raw": g["raw"], "y": g["y"],
                         "y_excess": (g["y"] - sum(labelled) / len(labelled)) if g["y"] is not None and labelled else None})  # fmt: skip
    rows.sort(key=lambda r: (r["date"], r["inst"]))
    return {"rows": rows, "dates": sorted({r["date"] for r in rows}), "dropped_dates": dropped_dates, "horizon": horizon, "delay": delay,
            "min_names": min_names, "features": list(FEATURES)}  # fmt: skip


def dataset_hash(panel: dict) -> str:
    """Huella del panel: mismos datos y parámetros → mismo hash (reproducibilidad, plan 10.5 punto 10)."""
    payload = [
        [
            r["date"].isoformat(),
            r["inst"],
            [round(r["x"][k], 10) for k in panel["features"]],
            None if r["y"] is None else round(r["y"], 10),
        ]
        for r in panel["rows"]
    ]
    blob = json.dumps(
        {"h": panel["horizon"], "d": panel["delay"], "m": panel["min_names"], "rows": payload},
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode()).hexdigest()
