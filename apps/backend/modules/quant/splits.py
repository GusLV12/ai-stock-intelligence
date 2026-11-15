"""Partición temporal con purga: entrenamiento < validación < prueba, siempre en orden cronológico.

La etiqueta de la fecha t se conoce hasta t+delay+horizon. Si la última fecha de entrenamiento estuviera pegada a la
primera de validación, su etiqueta se solaparía con el periodo de validación (fuga de información). Por eso se
descartan `delay+horizon` fechas al final de cada segmento anterior (purga). Los hiperparámetros se eligen con
validación; la prueba se mira una sola vez."""


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
