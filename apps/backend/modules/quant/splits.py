"""Partición temporal con purga: entrenamiento < validación < prueba, siempre en orden cronológico.

La etiqueta de la fecha t se conoce hasta t+delay+horizon. Si la última fecha de entrenamiento estuviera pegada a la
primera de validación, su etiqueta se solaparía con el periodo de validación (fuga de información). Por eso se
descartan `delay+horizon` fechas al final de cada segmento anterior (purga). Los hiperparámetros se eligen con
validación; la prueba se mira una sola vez."""

MIN_BLOCK, MIN_TRAIN = 20, 30


class SplitError(ValueError):
    pass


def temporal_split(dates: list, horizon: int, delay: int = 1, train: float = 0.6, valid: float = 0.2) -> dict:
    """`dates`: fechas ascendentes con etiqueta conocida. Devuelve conjuntos de fechas y el hueco aplicado."""
    if not (0 < train < 1 and 0 < valid < 1 and train + valid < 1):
        raise SplitError(
            "Las proporciones de entrenamiento y validación deben ser positivas y sumar menos de 1."
        )
    if dates != sorted(set(dates)):
        raise SplitError("Las fechas deben ser únicas y ascendentes.")
    gap = delay + horizon
    n = len(dates)
    i_valid, i_test = int(n * train), int(n * (train + valid))
    tr, va, te = dates[: max(i_valid - gap, 0)], dates[i_valid : max(i_test - gap, i_valid)], dates[i_test:]
    if not (tr and va and te):
        raise SplitError(
            f"Pocas fechas ({n}) para particionar con purga de {gap}: se necesita más historial."
        )
    return {"train": tr, "valid": va, "test": te, "gap": gap,
            "purged": {"before_valid": dates[max(i_valid - gap, 0) : i_valid], "before_test": dates[max(i_test - gap, i_valid) : i_test]}}  # fmt: skip


def segment(rows: list[dict], dates: list) -> list[dict]:
    ds = set(dates)
    return [r for r in rows if r["date"] in ds]


def _fold(history: list, test: list, gap: int, valid_frac: float) -> dict:
    """Entrenamiento/validación con la historia disponible antes de `test` (ya purgada)."""
    n_valid = max(int(len(history) * valid_frac), 1)
    train = history[: max(len(history) - n_valid - gap, 0)]
    valid = history[len(history) - n_valid :]
    return {"train": train, "valid": valid, "test": test, "gap": gap}


def walk_forward(
    dates: list,
    horizon: int,
    delay: int = 1,
    folds: int = 4,
    min_history: float = 0.4,
    valid_frac: float = 0.2,
) -> dict:
    """Validación walk-forward de ventana creciente: la zona de prueba (todo lo posterior a `min_history`) se parte en
    `folds` bloques contiguos; para cada bloque se entrena solo con lo anterior (menos `delay+horizonte` fechas de
    purga) y se valida con el tramo final de esa historia. Así se evalúa en varios periodos distintos, no en uno solo.
    Devuelve {"folds": [...], "live": ...}; `live` es el ajuste con toda la historia, para puntuar la fecha más reciente."""
    if folds < 2:
        raise SplitError("Walk-forward necesita al menos 2 bloques.")
    if not (0.1 <= min_history < 0.9 and 0 < valid_frac < 0.5):
        raise SplitError("Proporciones de walk-forward fuera de rango.")
    if dates != sorted(set(dates)):
        raise SplitError("Las fechas deben ser únicas y ascendentes.")
    gap, n = delay + horizon, len(dates)
    start = int(n * min_history)
    size = (n - start) // folds
    if size < MIN_BLOCK:
        raise SplitError(
            f"Pocas fechas ({n}) para {folds} bloques de prueba de al menos {MIN_BLOCK}: se necesita más historial o menos bloques."
        )
    out = []
    for k in range(folds):
        b0 = start + k * size
        b1 = n if k == folds - 1 else b0 + size
        history = dates[: max(b0 - gap, 0)]
        f = _fold(history, dates[b0:b1], gap, valid_frac)
        if len(f["train"]) < MIN_TRAIN or not f["valid"]:
            raise SplitError(
                f"Poca historia de entrenamiento para el bloque {k + 1}: se necesita más historial."
            )
        out.append(f)
    live = _fold(dates, [], gap, valid_frac)
    return {"folds": out, "live": live, "gap": gap}
