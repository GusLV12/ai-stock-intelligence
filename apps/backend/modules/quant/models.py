"""Modelos de puntaje: baselines sencillos y regresión ridge. Cada uno recibe filas (con factores `x` ya normalizados
por fecha) y devuelve un puntaje por fila: mayor puntaje = se espera mejor retorno relativo ese día.

Un modelo complejo solo se justifica si supera a los baselines fuera de muestra (criterio de promoción)."""

import random

from .features import FEATURES

BASELINES = {
    "momentum_20": ("Momentum 20 sesiones", lambda r: r["x"]["ret_20"]),
    "momentum_60": ("Momentum 60 sesiones", lambda r: r["x"]["ret_60"]),
    "reversal_5": ("Reversión a 5 sesiones", lambda r: -r["x"]["ret_5"]),
    "low_vol": ("Baja volatilidad", lambda r: -r["x"]["vol_20"]),
}
RIDGE_GRID = (0.1, 1.0, 10.0, 100.0)


def baseline_scores(name: str, rows: list[dict], seed: int = 0) -> list[float]:
    if name == "random":  # control: debe dar IC ≈ 0; determinista por (semilla, fecha, instrumento)
        return [random.Random(f"{seed}|{r['date']}|{r['inst']}").random() for r in rows]
    if name not in BASELINES:
        raise KeyError(name)
    f = BASELINES[name][1]
    return [f(r) for r in rows]


def baseline_label(name: str) -> str:
    return "Aleatorio (control)" if name == "random" else BASELINES[name][0]


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Resuelve a·w = b por eliminación gaussiana con pivoteo parcial."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12:
            raise ValueError("Sistema singular.")
        m[c], m[p] = m[p], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            for k in range(c, n + 1):
                m[r][k] -= f * m[c][k]
    w = [0.0] * n
    for i in range(n - 1, -1, -1):
        w[i] = (m[i][n] - sum(m[i][k] * w[k] for k in range(i + 1, n))) / m[i][i]
    return w


def fit_ridge(rows: list[dict], lam: float) -> dict:
    """Ridge sobre y_excess (retorno menos el promedio del día). El intercepto no se penaliza."""
    train = [r for r in rows if r["y_excess"] is not None]
    if len(train) < len(FEATURES) + 2:
        raise ValueError("Muy pocas filas para ajustar el modelo.")
    p = len(FEATURES) + 1
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for r in train:
        v = [r["x"][k] for k in FEATURES] + [1.0]
        for i in range(p):
            xty[i] += v[i] * r["y_excess"]
            for j in range(p):
                xtx[i][j] += v[i] * v[j]
    for i in range(p - 1):
        xtx[i][i] += lam * len(train)
    w = _solve(xtx, xty)
    return {"lambda": lam, "weights": dict(zip(FEATURES, w[:-1], strict=True)), "intercept": w[-1]}


def ridge_scores(model: dict, rows: list[dict]) -> list[float]:
    return [sum(model["weights"][k] * r["x"][k] for k in FEATURES) + model["intercept"] for r in rows]
