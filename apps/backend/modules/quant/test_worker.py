import random

import pytest

from . import worker
from .dataset import build_panel
from .metrics import ic_by_date, summarize
from .splits import segment, temporal_split
from .test_metrics_models import mundo

requiere_worker = pytest.mark.skipif(not worker.available(), reason="entorno de LightGBM no instalado")


def preparar(seed=7, persistente=True):
    panel = build_panel(mundo(seed, n_names=15, n=400, persistente=persistente), 20)
    labelled = [d for d in panel["dates"] if any(r["y"] is not None for r in panel["rows"] if r["date"] == d)]
    s = temporal_split(labelled, 20)
    return {k: segment(panel["rows"], s[k]) for k in ("train", "valid", "test")}


def test_sin_entorno_se_declara_no_disponible(monkeypatch):
    monkeypatch.setenv("QUANT_WORKER_PYTHON", "C:/no/existe/python.exe")
    assert worker.available() is False
    with pytest.raises(worker.WorkerUnavailable, match="no está instalado"):
        worker.lightgbm_scores([], [], {}, 0)


@requiere_worker
def test_lightgbm_detecta_senal_plantada_y_es_reproducible():
    seg = preparar()
    a = worker.lightgbm_scores(seg["train"], seg["valid"], {"test": seg["test"]}, seed=1)
    b = worker.lightgbm_scores(seg["train"], seg["valid"], {"test": seg["test"]}, seed=1)
    assert a["predictions"]["test"] == b["predictions"]["test"]  # misma semilla y datos: mismas predicciones
    ric = summarize(ic_by_date(seg["test"], a["predictions"]["test"]), 20)["rank_ic"]["mean"]
    assert ric > 0.1
    assert (
        a["versions"]["lightgbm"]
        and a["best_iteration"] >= 1
        and set(a["feature_importance_gain"]) == set(preparar_features())
    )


def preparar_features():
    from .features import FEATURES

    return FEATURES


@requiere_worker
def test_lightgbm_en_ruido_puro_no_inventa_senal():
    rics = []
    for seed in (11, 12, 13):
        seg = preparar(seed, persistente=False)
        r = worker.lightgbm_scores(seg["train"], seg["valid"], {"test": seg["test"]}, seed=1)
        rics.append(summarize(ic_by_date(seg["test"], r["predictions"]["test"]), 20)["rank_ic"]["mean"])
    assert abs(sum(rics) / len(rics)) < 0.1


@requiere_worker
def test_pocas_filas_falla_con_mensaje_claro():
    seg = preparar()
    with pytest.raises(worker.WorkerUnavailable, match="Muy pocas filas"):
        worker.lightgbm_scores(seg["train"][:10], seg["valid"], {"test": seg["test"]}, seed=1)


def test_el_azar_de_la_prueba_es_local():
    assert random.Random(1).random() == random.Random(1).random()


# ---------------------------------------------------------------- Qlib real (entorno Python 3.11)
requiere_qlib = pytest.mark.skipif(not worker.qlib_available(), reason="entorno de Qlib no instalado")


def test_sin_entorno_de_qlib_se_declara_no_disponible(monkeypatch):
    monkeypatch.setenv("QUANT_QLIB_PYTHON", "C:/no/existe/python.exe")
    assert worker.qlib_available() is False
    with pytest.raises(worker.WorkerUnavailable, match="Qlib no está instalado"):
        worker.qlib_scores({}, [], [], 20, 1, 0)


@requiere_qlib
def test_qlib_real_detecta_senal_plantada_y_es_reproducible():
    from .runner import run_experiment

    series = mundo(7, n_names=16, n=420, persistente=True)
    cfg = {"horizon": 20, "folds": 1, "seed": 3, "models": ["momentum_60", "ridge", "qlib_lgbm"], "top_k": 4}
    a = run_experiment(series, cfg)
    b = run_experiment(series, cfg)
    q = a["models"]["qlib_lgbm"]
    assert "unavailable" not in q, q.get("unavailable")
    assert q["alpha158_features"] == 158 and q["versions"]["qlib"] and q["rows_without_score"] == 0
    assert q["test"]["rank_ic"]["mean"] > 0.1  # el ruido no tapa una deriva persistente
    assert a["result_hash"] == b["result_hash"]  # misma semilla y datos: mismo resultado
    assert q["bars_without_ohlc"] > 0  # estas series sintéticas no traen velas completas: se declara


@requiere_qlib
def test_qlib_en_ruido_puro_no_inventa_senal():
    from .runner import run_experiment

    r = run_experiment(
        mundo(31, n_names=16, n=420, persistente=False),
        {"horizon": 20, "folds": 1, "seed": 3, "models": ["ridge", "qlib_lgbm"]},
    )
    assert abs(r["models"]["qlib_lgbm"]["test"]["rank_ic"]["mean"]) < 0.12
