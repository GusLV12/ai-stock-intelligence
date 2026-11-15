"""Puente hacia el trabajador LightGBM (entorno Python separado). Si no está instalado, el modelo se declara no
disponible; el resto del laboratorio sigue funcionando."""

import json
import os
import subprocess
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
            proc = subprocess.run(
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
