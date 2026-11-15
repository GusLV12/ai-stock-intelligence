"""Trabajador LightGBM: entrena con un conjunto de entrenamiento, para por validación y puntúa conjuntos dados.

Corre en su propio entorno (workers/qlib/.venv) para no mezclar dependencias científicas con Django. No toca la base
de datos ni la red: lee un JSON, escribe un JSON. La evaluación (IC, backtest, promoción) la hace SIEMPRE el backend
con su propio código, igual para todos los modelos; este trabajador solo produce puntajes.

Uso: python lgbm_worker.py entrada.json salida.json
"""

import json
import sys

import lightgbm as lgb
import numpy as np

DEFAULT_PARAMS = {
    "objective": "regression",
    "learning_rate": 0.05,
    "num_leaves": 7,
    "min_data_in_leaf": 20,
    "feature_fraction": 0.8,
    "lambda_l2": 1.0,
    "verbosity": -1,
    "num_threads": 1,  # un hilo + modo determinista: mismos datos y semilla, mismas predicciones
    "deterministic": True,
    "force_row_wise": True,
}
MAX_ROUNDS, EARLY_STOP = 300, 30


def main(src: str, dst: str) -> None:
    with open(src, encoding="utf-8") as f:
        job = json.load(f)
    seed = int(job.get("seed", 0))
    params = {**DEFAULT_PARAMS, **job.get("params", {}), "seed": seed}
    xtr, ytr = np.array(job["train"]["x"], dtype=float), np.array(job["train"]["y"], dtype=float)
    xva, yva = np.array(job["valid"]["x"], dtype=float), np.array(job["valid"]["y"], dtype=float)
    if len(xtr) < 50 or len(xva) < 10:
        raise SystemExit("Muy pocas filas para LightGBM (se necesitan ≥ 50 de entrenamiento y ≥ 10 de validación).")
    dtr = lgb.Dataset(xtr, label=ytr, feature_name=job["features"], free_raw_data=False)
    dva = lgb.Dataset(xva, label=yva, reference=dtr)
    booster = lgb.train(params, dtr, num_boost_round=MAX_ROUNDS, valid_sets=[dva], callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False)])
    preds = {name: booster.predict(np.array(x, dtype=float), num_iteration=booster.best_iteration).tolist() for name, x in job["predict"].items() if x}
    imp = booster.feature_importance(importance_type="gain")
    out = {
        "predictions": preds,
        "best_iteration": int(booster.best_iteration),
        "feature_importance_gain": {n: float(v) for n, v in zip(job["features"], imp, strict=True)},
        "params": params,
        "versions": {"lightgbm": lgb.__version__, "numpy": np.__version__},
    }
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
