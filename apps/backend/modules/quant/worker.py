"""Puente hacia el trabajador LightGBM (entorno Python separado). Si no está instalado, el modelo se declara no
disponible; el resto del laboratorio sigue funcionando."""

import json
import os
import subprocess  # nosec B404 - se lanza solo el intérprete del entorno aislado, con rutas propias
import tempfile
from pathlib import Path

from .features import FEATURES

ROOT = Path(__file__).resolve().parents[4]
WORKER = ROOT / "workers" / "qlib" / "lgbm_worker.py"
TIMEOUT_SECONDS = 180


class WorkerUnavailable(RuntimeError):
    pass


def worker_python() -> Path | None:
    env = os.environ.get("QUANT_WORKER_PYTHON")
    if env:
        return Path(env) if Path(env).exists() else None
    for rel in ("Scripts/python.exe", "bin/python"):
        p = ROOT / "workers" / "qlib" / ".venv" / rel
        if p.exists():
            return p
    return None


def available() -> bool:
    return worker_python() is not None and WORKER.exists()


def lightgbm_scores(
    train: list[dict],
    valid: list[dict],
    predict: dict[str, list[dict]],
    seed: int,
    params: dict | None = None,
) -> dict:
    """Entrena con `train` (objetivo: retorno menos el promedio del día), detiene con `valid` y puntúa cada conjunto de `predict`."""
    py = worker_python()
    if py is None or not WORKER.exists():
        raise WorkerUnavailable(
            "El entorno de LightGBM no está instalado (ver workers/qlib/requirements.txt)."
        )

    def xs(rows):
        return [[r["x"][k] for k in FEATURES] for r in rows]

    def usable(rows):
        return [r for r in rows if r["y_excess"] is not None]

    tr, va = usable(train), usable(valid)
    job = {"features": FEATURES, "seed": seed, "params": params or {},
           "train": {"x": xs(tr), "y": [r["y_excess"] for r in tr]}, "valid": {"x": xs(va), "y": [r["y_excess"] for r in va]},
           "predict": {k: xs(v) for k, v in predict.items()}}  # fmt: skip
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.json", Path(tmp) / "out.json"
        src.write_text(json.dumps(job), encoding="utf-8")
        try:
            proc = subprocess.run(  # nosec B603 - sin shell; ejecutable y archivos son rutas controladas por nosotros
                [str(py), "-I", str(WORKER), str(src), str(dst)],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkerUnavailable("LightGBM excedió el tiempo límite.") from exc
        if proc.returncode != 0 or not dst.exists():
            raise WorkerUnavailable(
                (proc.stderr or proc.stdout or "falló sin mensaje").strip().splitlines()[-1][:300]
            )
        return json.loads(dst.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------------------------------------------
# Qlib real (Alpha158 + LightGBM de Qlib) en su propio entorno Python 3.11
QLIB_WORKER = ROOT / "workers" / "qlib" / "qlib_worker.py"
QLIB_TIMEOUT_SECONDS = 900


def qlib_python() -> Path | None:
    env = os.environ.get("QUANT_QLIB_PYTHON")
    if env:
        return Path(env) if Path(env).exists() else None
    for rel in ("Scripts/python.exe", "bin/python"):
        p = ROOT / "workers" / "qlib" / ".venv311" / rel
        if p.exists():
            return p
    return None


def qlib_available() -> bool:
    return qlib_python() is not None and QLIB_WORKER.exists()


def qlib_scores(series: dict, train: list, valid: list, horizon: int, delay: int, seed: int) -> dict:
    """Entrena Alpha158 + LightGBM de Qlib con las fechas `train`, para con `valid` y devuelve {(fecha, instrumento): puntaje}
    para todas las fechas desde el inicio de `valid` hasta el final de los datos (validación, prueba y fecha más reciente).
    Los factores de Qlib se normalizan solo con la ventana de entrenamiento (no con datos posteriores)."""
    py = qlib_python()
    if py is None or not QLIB_WORKER.exists():
        raise WorkerUnavailable("El entorno de Qlib no está instalado (ver workers/qlib/README.md).")
    bars, missing = {}, 0
    for inst, rows in series.items():
        out = []
        for b in rows:
            c = b["close"]
            if b.get("open") is None or b.get("high") is None or b.get("low") is None:
                missing += 1
            out.append({"date": str(b["date"]), "open": b.get("open") or c, "high": b.get("high") or c, "low": b.get("low") or c,
                        "close": c, "volume": float(b.get("volume") or 0), "factor": 1.0})  # fmt: skip
        bars[f"I{inst}"] = out
    all_dates = sorted({b["date"] for rows in series.values() for b in rows})
    job = {"bars": bars, "horizon": horizon, "delay": delay, "seed": seed, "start": str(all_dates[0]), "end": str(all_dates[-1]),
           "segments": {"train": [str(train[0]), str(train[-1])], "valid": [str(valid[0]), str(valid[-1])], "predict": [str(valid[0]), str(all_dates[-1])]}}  # fmt: skip
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.json", Path(tmp) / "out.json"
        src.write_text(json.dumps(job), encoding="utf-8")
        try:
            proc = subprocess.run(  # nosec B603 - sin shell; ejecutable y archivos son rutas controladas por nosotros
                [str(py), "-I", str(QLIB_WORKER), str(src), str(dst)],
                capture_output=True,
                text=True,
                timeout=QLIB_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkerUnavailable("Qlib excedió el tiempo límite.") from exc
        if proc.returncode != 0 or not dst.exists():
            raise WorkerUnavailable(
                (proc.stderr or proc.stdout or "falló sin mensaje").strip().splitlines()[-1][:300]
            )
        res = json.loads(dst.read_text(encoding="utf-8"))
    res["scores"] = {(p["date"], int(p["inst"][1:])): p["score"] for p in res.pop("predictions")}
    res["bars_without_ohlc"] = missing
    return res
