import random
from datetime import date, timedelta

import pytest

from .dataset import build_panel
from .metrics import ic_by_date, pearson, spearman, summarize
from .models import RIDGE_GRID, baseline_scores, fit_ridge, ridge_scores
from .splits import segment, temporal_split


def test_pearson_y_spearman_a_mano():
    assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)
    assert pearson([1, 1, 1], [1, 2, 3]) is None  # sin dispersión no hay correlación
    assert spearman([1, 2, 3, 4], [1, 4, 9, 100]) == pytest.approx(1.0)  # monótona aunque no lineal
    assert spearman([1, 2, 2, 4], [1, 2, 3, 4]) == pytest.approx(
        0.9486832980505138
    )  # empates con rango promedio
    assert pearson([1, 2], [1, 2]) is None  # muy pocos datos


def fila(d, inst, y):
    return {"date": d, "inst": inst, "y": y}


def test_ic_por_fecha_omite_dias_chicos_y_sin_etiqueta():
    d1, d2, d3 = date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)
    rows = (
        [fila(d1, i, float(i)) for i in range(6)]
        + [fila(d2, i, float(i)) for i in range(3)]
        + [fila(d3, i, None) for i in range(6)]
    )
    out = ic_by_date(rows, [float(i) for i in range(6)] + [0.0, 1.0, 2.0] + list(range(6)))
    assert [o["date"] for o in out] == [d1] and out[0]["rank_ic"] == pytest.approx(1.0)


def test_resumen_t_ajustado_es_mas_conservador():
    ics = [{"ic": 0.05 + (i % 3) * 0.01, "rank_ic": 0.04 + (i % 3) * 0.01, "n": 10} for i in range(60)]
    s = summarize(ics, horizon=20)
    assert s["rank_ic"]["t_adjusted"] < s["rank_ic"]["t_naive"]
    assert s["rank_ic"]["t_adjusted"] == pytest.approx(s["rank_ic"]["t_naive"] / (20**0.5))
    assert s["rank_ic"]["pct_positive"] == 1.0 and len(s["rank_ic_blocks"]) == 3


def mundo(seed, n_names=12, n=260, persistente=False):
    rng = random.Random(seed)
    out = {}
    for k in range(n_names):
        drift = rng.uniform(-0.003, 0.003) if persistente else 0.0
        p, d0, bars = 100.0, date(2024, 1, 1), []
        for i in range(n):
            p *= 1 + drift + rng.gauss(0, 0.01)
            bars.append({"date": d0 + timedelta(days=i), "close": p})
        out[k] = bars
    return out


def evalua(series, h=20):
    panel = build_panel(series, h)
    labelled = [d for d in panel["dates"] if any(r["y"] is not None for r in panel["rows"] if r["date"] == d)]
    s = temporal_split(labelled, h)
    return panel, s


def test_caminata_aleatoria_no_tiene_senal_aprovechable():
    ric = []
    for seed in range(5):  # varias semillas: el promedio del IC de un baseline en ruido puro debe rondar 0
        panel, s = evalua(mundo(seed))
        te = segment(panel["rows"], s["test"])
        ric.append(summarize(ic_by_date(te, baseline_scores("momentum_20", te)), 20)["rank_ic"]["mean"])
    assert abs(sum(ric) / len(ric)) < 0.08
    te = segment(panel["rows"], s["test"])
    azar = summarize(ic_by_date(te, baseline_scores("random", te, seed=1)), 20)["rank_ic"]["mean"]
    assert abs(azar) < 0.1


def test_senal_plantada_se_detecta_con_momentum_y_ridge():
    panel, s = evalua(mundo(7, persistente=True))
    te, tr = segment(panel["rows"], s["test"]), segment(panel["rows"], s["train"])
    mom = summarize(ic_by_date(te, baseline_scores("momentum_60", te)), 20)["rank_ic"]["mean"]
    assert mom > 0.15
    modelo = fit_ridge(tr, 1.0)
    ridge = summarize(ic_by_date(te, ridge_scores(modelo, te)), 20)["rank_ic"]["mean"]
    assert ridge > 0.15


def test_ridge_recupera_coeficientes_conocidos():
    rng = random.Random(0)
    rows = []
    for i in range(400):
        x = {
            k: rng.uniform(-0.5, 0.5)
            for k in ["ret_5", "ret_20", "ret_60", "vol_20", "dist_sma_20", "dist_sma_50", "dd_60", "rsi_14"]
        }
        rows.append({"x": x, "y_excess": 2.0 * x["ret_20"] - 1.0 * x["vol_20"] + 0.5, "date": 0, "inst": i})
    m = fit_ridge(rows, 1e-9)
    assert m["weights"]["ret_20"] == pytest.approx(2.0, abs=1e-3) and m["weights"]["vol_20"] == pytest.approx(
        -1.0, abs=1e-3
    )
    assert m["intercept"] == pytest.approx(0.5, abs=1e-3) and m["weights"]["ret_5"] == pytest.approx(
        0, abs=1e-3
    )
    assert RIDGE_GRID == (0.1, 1.0, 10.0, 100.0)
    # más penalización encoge los pesos
    assert abs(fit_ridge(rows, 10.0)["weights"]["ret_20"]) < abs(m["weights"]["ret_20"])


def test_ridge_con_pocas_filas_falla_claro():
    with pytest.raises(ValueError, match="pocas filas"):
        fit_ridge([], 1.0)


def test_aleatorio_es_determinista():
    rows = [{"date": date(2025, 1, 1), "inst": i} for i in range(5)]
    assert baseline_scores("random", rows, 3) == baseline_scores("random", rows, 3)
    assert baseline_scores("random", rows, 3) != baseline_scores("random", rows, 4)
    with pytest.raises(KeyError):
        baseline_scores("nada", rows)
