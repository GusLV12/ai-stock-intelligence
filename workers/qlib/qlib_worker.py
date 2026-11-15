"""Trabajador Qlib: factores Alpha158 + LightGBM de Qlib sobre las velas que le entrega el backend.

Corre en su propio entorno (workers/qlib/.venv311, Python 3.11 + pyqlib). No toca la base de datos ni la red: lee un
JSON con las velas y los rangos de fechas y escribe otro JSON con los puntajes. La evaluación (IC, backtest,
promoción) la hace SIEMPRE el backend con su propio código, igual para todos los modelos.

Pasos (los de Qlib): 1) se escribe un proveedor de datos binario de Qlib con las velas (OHLCV ajustado por
dividendos); 2) Alpha158 genera 158 factores solo con datos hasta cada fecha; 3) la etiqueta es el retorno a
`horizon` sesiones con ejecución `delay` sesiones después, igual que en el resto del laboratorio; 4) LightGBM se
entrena con `train`, se detiene con `valid` y puntúa `predict`.

Uso: python qlib_worker.py entrada.json salida.json
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

FIELDS = ["open", "high", "low", "close", "volume", "factor"]


def write_provider(root: Path, bars: dict) -> list[str]:
    """Escribe el formato binario de Qlib: calendars/day.txt, instruments/all.txt y features/<sym>/<campo>.day.bin
    (float32: primero el índice de calendario donde empieza la serie, luego los datos)."""
    dates = sorted({b["date"] for series in bars.values() for b in series})
    index = {d: i for i, d in enumerate(dates)}
    (root / "calendars").mkdir(parents=True)
    (root / "instruments").mkdir()
    (root / "calendars" / "day.txt").write_text("\n".join(dates) + "\n", encoding="utf-8")
    lines = []
    for sym, series in bars.items():
        s = sorted(series, key=lambda b: b["date"])
        lines.append(f"{sym}\t{s[0]['date']}\t{s[-1]['date']}")
        fdir = root / "features" / sym.lower()
        fdir.mkdir(parents=True)
        start = index[s[0]["date"]]
        n = index[s[-1]["date"]] - start + 1
        for field in FIELDS:
            arr = np.full(n, np.nan, dtype="<f")
            for b in s:
                arr[index[b["date"]] - start] = b[field]
            np.hstack([[start], arr]).astype("<f").tofile(fdir / f"{field}.day.bin")
    (root / "instruments" / "all.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dates


def main(src: str, dst: str) -> None:
    os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"  # el registro de métricas de Qlib se escribe solo en la carpeta temporal
    import qlib
    from qlib.contrib.data.handler import Alpha158
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.data.dataset import DatasetH

    with open(src, encoding="utf-8") as f:
        job = json.load(f)
    seed = int(job.get("seed", 0))
    seg = job["segments"]  # {"train": [ini, fin], "valid": [...], "predict": [...]}
    horizon, delay = int(job["horizon"]), int(job["delay"])
    tmp = tempfile.mkdtemp(prefix="qlib_worker_")
    try:
        root = Path(tmp) / "provider"
        write_provider(root, job["bars"])
        mlruns = (Path(tmp) / "mlruns").as_uri()
        exp_manager = {"class": "MLflowExpManager", "module_path": "qlib.workflow.expm", "kwargs": {"uri": mlruns, "default_exp_name": "quant"}}
        qlib.init(provider_uri=str(root), region="us", kernels=1, expression_cache=None, dataset_cache=None, logging_level="WARNING", exp_manager=exp_manager)
        label = [f"Ref($close, -{delay + horizon}) / Ref($close, -{delay}) - 1"]
        handler = Alpha158(
            instruments="all",
            start_time=job["start"],
            end_time=job["end"],
            fit_start_time=seg["train"][0],
            fit_end_time=seg["train"][1],
            label=(label, ["LABEL0"]),
            infer_processors=[{"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}}, {"class": "Fillna", "kwargs": {"fields_group": "feature"}}],
            learn_processors=[{"class": "DropnaLabel"}, {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}}],
        )
        ds = DatasetH(handler, {"train": tuple(seg["train"]), "valid": tuple(seg["valid"]), "predict": tuple(seg["predict"])})
        model = LGBModel(loss="mse", learning_rate=0.05, num_leaves=15, max_depth=5, subsample=0.8, colsample_bytree=0.8,
                         lambda_l2=10.0, num_threads=1, seed=seed, deterministic=True, force_row_wise=True, verbosity=-1)  # fmt: skip
        model.fit(ds, num_boost_round=300, early_stopping_rounds=30, verbose_eval=False)
        pred = model.predict(ds, segment="predict")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out = [{"date": str(d.date()), "inst": str(i), "score": float(v)} for (d, i), v in pred.items()]
    with open(dst, "w", encoding="utf-8") as f:
        json.dump({"predictions": out, "features": 158, "versions": {"qlib": qlib.__version__, "pandas": pd.__version__, "numpy": np.__version__}}, f)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
    sys.stdout.flush()
    os._exit(0)  # evita que mlflow intente cerrar su registro temporal (ya borrado) al salir
