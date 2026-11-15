import random
from datetime import date, timedelta

import pytest

from .dataset import DatasetError, build_panel, dataset_hash
from .features import FEATURES, MIN_HISTORY, feature_series
from .splits import SplitError, segment, temporal_split


def walk(seed, n=150, start=100.0, drift=0.0, vol=0.01):
    rng = random.Random(seed)
    d0, p, out = date(2025, 1, 1), start, []
    for i in range(n):
        p *= 1 + drift + rng.gauss(0, vol)
        out.append({"date": d0 + timedelta(days=i), "close": p})
    return out


def universe(n_names=8, n=150):
    return {k: walk(k, n) for k in range(1, n_names + 1)}


def test_factores_a_mano():
    closes = [100.0 + i for i in range(80)]  # tendencia lineal
    f = feature_series(closes)
    assert f[MIN_HISTORY - 2] is None and f[MIN_HISTORY - 1] is not None
    x = f[70]
    assert x["ret_5"] == pytest.approx(170 / 165 - 1) and x["ret_20"] == pytest.approx(170 / 150 - 1)
    assert x["ret_60"] == pytest.approx(170 / 110 - 1)
    assert x["dist_sma_20"] == pytest.approx(170 / 160.5 - 1) and x["dd_60"] == 0.0 and x["rsi_14"] == 1.0


def test_factores_no_miran_al_futuro():
    base = walk(3, 140)
    f1 = feature_series([b["close"] for b in base])
    alterada = [b["close"] for b in base[:100]] + [b["close"] * 5 for b in base[100:]]
    f2 = feature_series(alterada)
    for i in range(100):  # lo anterior a la alteración no cambia
        assert f1[i] == f2[i]


def test_etiqueta_con_retraso_de_ejecucion():
    u = universe()
    p = build_panel(u, horizon=5, delay=1)
    inst, d = 1, p["rows"][0]["date"]
    i = [b["date"] for b in u[inst]].index(d)
    r = next(r for r in p["rows"] if r["date"] == d and r["inst"] == inst)
    assert r["y"] == pytest.approx(
        u[inst][i + 6]["close"] / u[inst][i + 1]["close"] - 1
    )  # entra en t+1, sale en t+6
    last = [r for r in p["rows"] if r["date"] == p["dates"][-1]]
    assert all(r["y"] is None for r in last)  # sin futuro no hay etiqueta


def test_la_etiqueta_no_contamina_los_factores():
    u = universe()
    a = build_panel(u, 5)
    u2 = {k: list(v) for k, v in u.items()}
    for k in u2:  # cambiar solo los últimos 10 cierres: los factores de fechas antiguas no se mueven
        for b in u2[k][-10:]:
            b["close"] *= 1.5
    b = build_panel(u2, 5)
    viejas = {(r["date"], r["inst"]): r["x"] for r in a["rows"] if r["date"] <= a["dates"][-12]}
    assert all(
        r["x"] == viejas[(r["date"], r["inst"])] for r in b["rows"] if (r["date"], r["inst"]) in viejas
    )


def test_normalizacion_por_fecha_en_rango():
    p = build_panel(universe(6), 5)
    day = [r for r in p["rows"] if r["date"] == p["dates"][10]]
    vals = sorted(r["x"]["ret_20"] for r in day)
    assert vals[0] == -0.5 and vals[-1] == 0.5 and sum(vals) == pytest.approx(0, abs=1e-9)


def test_fechas_con_pocas_emisoras_se_descartan_y_se_declaran():
    u = universe(4)
    p = build_panel(u, 5, min_names=5)
    assert p["rows"] == [] and p["dropped_dates"]


def test_parametros_invalidos():
    with pytest.raises(DatasetError):
        build_panel(universe(), 0)


def test_hash_reproducible_y_sensible():
    a, b = build_panel(universe(), 5), build_panel(universe(), 5)
    assert dataset_hash(a) == dataset_hash(b)
    assert dataset_hash(a) != dataset_hash(build_panel(universe(), 20))


def test_particion_con_purga_sin_solapes():
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(100)]
    s = temporal_split(dates, horizon=5, delay=1)
    assert max(s["train"]) < min(s["valid"]) and max(s["valid"]) < min(s["test"])
    assert (min(s["valid"]) - max(s["train"])).days > 6  # ≥ delay+horizon fechas de hueco
    assert (min(s["test"]) - max(s["valid"])).days > 6
    assert s["gap"] == 6 and len(s["purged"]["before_valid"]) == 6
    assert len(s["train"]) + len(s["valid"]) + len(s["test"]) + 12 == 100


def test_particion_rechaza_poco_historial_y_proporciones_malas():
    dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(10)]
    with pytest.raises(SplitError):
        temporal_split(dates, 5)
    with pytest.raises(SplitError):
        temporal_split(dates, 1, train=0.8, valid=0.3)
    with pytest.raises(SplitError):
        temporal_split(dates[::-1], 1)


def test_segment_filtra_filas():
    p = build_panel(universe(), 5)
    ds = p["dates"][:3]
    assert {r["date"] for r in segment(p["rows"], ds)} == set(ds)
    assert FEATURES == p["features"]
