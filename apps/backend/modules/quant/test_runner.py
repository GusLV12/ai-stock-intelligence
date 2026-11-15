import copy

import pytest

from . import promotion, worker
from .runner import ExperimentError, run_experiment
from .test_metrics_models import mundo

CONFIG = {"horizon": 20, "seed": 3, "top_k": 4}


def corre(series, **kw):
    return run_experiment(series, {**CONFIG, **kw.pop("config", {})}, **kw)


def test_senal_plantada_se_promueve_a_seguimiento():
    # la deriva persistente se aprende (ridge) y los baselines débiles no la capturan
    r = corre(
        mundo(7, n_names=25, n=500, persistente=True), config={"models": ["reversal_5", "random", "ridge"]}
    )
    p = r["promotion"]
    assert r["candidate"] == "ridge" and p["verdict"] == promotion.PROMOTE, p["reasons"]
    assert all(c["passed"] for c in p["checks"])
    assert r["models"]["ridge"]["backtest"]["excess_vs_equal_weight"] > 0
    assert abs(r["models"]["random"]["test"]["rank_ic"]["mean"]) < 0.1  # el control aleatorio no tiene señal


def test_un_baseline_igual_de_bueno_impide_la_promocion():
    # con momentum de 60 sesiones ya se captura toda la señal: un modelo más complejo no aporta y no se promueve
    r = corre(mundo(7, n_names=25, n=500, persistente=True), config={"models": ["momentum_60", "ridge"]})
    assert r["promotion"]["verdict"] == promotion.NOT_PROMOTED
    assert any("baseline" in x for x in r["promotion"]["reasons"])


@pytest.mark.parametrize("seed", [21, 22, 23, 24])
def test_ruido_puro_no_se_promueve(seed):
    r = corre(
        mundo(seed, n_names=25, n=500, persistente=False),
        config={"models": ["momentum_20", "random", "ridge"]},
    )
    assert r["promotion"]["verdict"] == promotion.NOT_PROMOTED, r["promotion"]["checks"]
    assert r["promotion"]["reasons"]


def test_universo_chico_da_datos_insuficientes():
    r = corre(mundo(1, n_names=8, n=300), config={"models": ["momentum_20", "ridge"]})
    assert r["promotion"]["verdict"] == promotion.INSUFFICIENT
    assert any("Emisoras" in x for x in r["promotion"]["reasons"])


def test_reproducible_misma_configuracion_mismo_resultado():
    s = mundo(5, n_names=12, n=300, persistente=True)
    cfg = {"models": ["momentum_20", "random", "ridge"]}
    a, b = corre(s, config=cfg), corre(copy.deepcopy(s), config=cfg)
    assert a["result_hash"] == b["result_hash"] and a["meta"]["dataset_hash"] == b["meta"]["dataset_hash"]
    assert (
        corre(s, config={**cfg, "seed": 99})["result_hash"] != a["result_hash"]
    )  # otra semilla: otro control aleatorio


def test_pesos_del_modelo_no_dependen_de_datos_posteriores_al_entrenamiento():
    s = mundo(9, n_names=15, n=400, persistente=True)
    base = corre(s, config={"models": ["ridge"]})
    alterada = {k: [dict(b) for b in v] for k, v in s.items()}
    for v in alterada.values():
        for b in v[-30:]:  # solo el final (periodo de prueba): jamás debe filtrarse al ajuste
            b["close"] *= 1.7
    otra = corre(alterada, config={"models": ["ridge"]})
    assert base["models"]["ridge"]["weights"] == otra["models"]["ridge"]["weights"]
    assert base["models"]["ridge"]["lambda"] == otra["models"]["ridge"]["lambda"]
    assert (
        base["models"]["ridge"]["valid"] == otra["models"]["ridge"]["valid"]
    )  # tampoco cambia la validación


def test_las_predicciones_actuales_son_de_la_ultima_fecha_sin_etiqueta():
    r = corre(mundo(5, n_names=12, n=300, persistente=True), config={"models": ["ridge"]})
    assert len(r["latest_scores"]) == 12 and r["latest_scores"][0]["score"] >= r["latest_scores"][-1]["score"]


def test_exige_un_candidato_y_valida_parametros():
    s = mundo(1, n_names=10, n=300)
    with pytest.raises(ExperimentError, match="aprenda"):
        corre(s, config={"models": ["momentum_20"]})
    with pytest.raises(ExperimentError, match="horizonte"):
        corre(s, config={"horizon": 7})
    with pytest.raises(ExperimentError, match="desconocido"):
        corre(s, config={"models": ["ridge", "inventado"]})
    with pytest.raises(ExperimentError, match="Pocas fechas|historial"):
        corre(mundo(1, n_names=10, n=90), config={"models": ["ridge"]})


def test_lightgbm_no_disponible_no_rompe_el_experimento(monkeypatch):
    monkeypatch.setenv("QUANT_WORKER_PYTHON", "C:/no/existe/python.exe")
    r = corre(mundo(5, n_names=12, n=300, persistente=True), config={"models": ["ridge", "lightgbm"]})
    assert "unavailable" in r["models"]["lightgbm"] and r["candidate"] == "ridge"


@pytest.mark.skipif(not worker.available(), reason="entorno de LightGBM no instalado")
def test_lightgbm_participa_y_es_reproducible():
    s = mundo(7, n_names=20, n=450, persistente=True)
    a = corre(s, config={"models": ["momentum_60", "ridge", "lightgbm"]})
    b = corre(s, config={"models": ["momentum_60", "ridge", "lightgbm"]})
    assert a["models"]["lightgbm"]["test"]["rank_ic"]["mean"] > 0.1 and a["result_hash"] == b["result_hash"]


def test_promocion_unidades():
    def resumen(ric, t, pos):
        return {"rank_ic": {"mean": ric, "t_adjusted": t, "pct_positive": pos}, "n_dates": 80}

    cand = {"name": "m", "valid": resumen(0.03, 2, 0.6), "test": resumen(0.05, 3.0, 0.7)}
    base = {"name": "momentum_20", "test": resumen(0.02, 1, 0.5)}
    ok = promotion.evaluate(cand, base, 80, 30, 0.02)
    assert ok["verdict"] == promotion.PROMOTE
    # cada criterio por separado hace fallar el veredicto
    assert (
        promotion.evaluate({**cand, "test": resumen(0.01, 3.0, 0.7)}, base, 80, 30, 0.02)["verdict"]
        == promotion.NOT_PROMOTED
    )
    assert (
        promotion.evaluate({**cand, "test": resumen(0.05, 1.5, 0.7)}, base, 80, 30, 0.02)["verdict"]
        == promotion.NOT_PROMOTED
    )
    assert (
        promotion.evaluate({**cand, "valid": resumen(-0.01, 2, 0.6)}, base, 80, 30, 0.02)["verdict"]
        == promotion.NOT_PROMOTED
    )
    assert (
        promotion.evaluate(cand, {"name": "b", "test": resumen(0.06, 1, 0.5)}, 80, 30, 0.02)["verdict"]
        == promotion.NOT_PROMOTED
    )
    assert promotion.evaluate(cand, base, 80, 30, -0.01)["verdict"] == promotion.NOT_PROMOTED
    assert promotion.evaluate(cand, base, 80, 30, None)["verdict"] == promotion.NOT_PROMOTED
    assert promotion.evaluate(cand, base, 10, 30, 0.02)["verdict"] == promotion.INSUFFICIENT
    assert promotion.evaluate(cand, base, 80, 5, 0.02)["verdict"] == promotion.INSUFFICIENT


def test_correccion_por_pruebas_multiples():
    assert promotion.required_t(2.0, 1) == 2.0
    assert promotion.required_t(2.0, 20) == pytest.approx(3.0233, abs=1e-3)  # más intentos, umbral más alto
    cand = {
        "name": "m",
        "valid": {"rank_ic": {"mean": 0.03}},
        "test": {"rank_ic": {"mean": 0.05, "t_adjusted": 2.5, "pct_positive": 0.7}, "n_dates": 80},
    }
    base = {"name": "b", "test": {"rank_ic": {"mean": 0.01}}}
    assert promotion.evaluate(cand, base, 80, 30, 0.02, trials=1)["verdict"] == promotion.PROMOTE
    assert promotion.evaluate(cand, base, 80, 30, 0.02, trials=20)["verdict"] == promotion.NOT_PROMOTED
