import random
from datetime import date, timedelta
from decimal import Decimal as D
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import models
from rest_framework.test import APIClient

from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.pricing.history_models import DailyClose

from .models import QuantExperiment

BASE = "/api/v1/quant"


def sembrar(n_names=22, n=480, persistente=True, seed=7, provider="YAHOO_UNOFFICIAL"):
    rng = random.Random(seed)
    ids = []
    for k in range(n_names):
        ins = Instrument.objects.create(asset_class="STOCK", name=f"E{k}", quantity_scale=8)
        InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=f"T{k}")
        drift = rng.uniform(-0.003, 0.003) if persistente else 0.0
        p, rows = 100.0, []
        for i in range(n):
            p *= 1 + drift + rng.gauss(0, 0.01)
            rows.append(
                DailyClose(
                    instrument=ins,
                    date=date(2024, 1, 1) + timedelta(days=i),
                    close=D(str(round(p, 4))),
                    currency="USD",
                    provider=provider,
                )
            )
        DailyClose.objects.bulk_create(rows)
        ids.append(ins.id)
    return ids


@pytest.fixture
def ctx(db):
    user = get_user_model().objects.create_user("ana", password="x")
    c = APIClient()
    c.force_authenticate(user)
    return c, user


def post(c, body=None):
    return c.post(
        f"{BASE}/experiments",
        {"models": ["reversal_5", "random", "ridge"], "seed": 3, **(body or {})},
        format="json",
    )


def test_resumen_del_laboratorio(ctx):
    c, _ = ctx
    sembrar(n_names=6, n=100)
    o = c.get(f"{BASE}/overview").json()
    assert len(o["universe"]) == 6 and o["universe"][0]["ticker"] == "T0" and o["universe"][0]["bars"] == 100
    assert (
        o["horizons"] == [5, 20]
        and o["criteria"]["min_test_dates"] == 60
        and "No es una recomendación" in o["disclaimer"]
    )
    assert {m["id"] for m in o["models"]} >= {"ridge", "lightgbm", "random"}


def test_experimento_completo_se_guarda_con_veredicto_y_huellas(ctx):
    c, user = ctx
    sembrar()
    r = post(c)
    assert r.status_code == 201, r.json()
    d = r.json()
    assert d["verdict"] == "PROMOVER_A_SEGUIMIENTO" and d["candidate"] == "ridge" and d["trials"] == 1
    assert (
        len(d["dataset_hash"]) == 64
        and len(d["result_hash"]) == 64
        and d["config"]["as_of"]
        and len(d["config"]["instrument_ids"]) == 22
    )
    res = d["result"]
    assert res["meta"]["tickers"][str(d["config"]["instrument_ids"][0])] == "T0"
    assert (
        res["meta"]["split"]["gap_sessions"] == 21
        and res["models"]["ridge"]["backtest"]["strategy"]["equity"]
    )
    assert (
        res["models"]["random"]["kind"] == "baseline"
        and res["promotion"]["checks"]
        and res["meta"]["caveats"]
    )
    assert QuantExperiment.objects.get(pk=d["id"]).user == user
    assert [e["id"] for e in c.get(f"{BASE}/experiments").json()] == [d["id"]]
    assert c.get(f"{BASE}/experiments/{d['id']}").json()["result_hash"] == d["result_hash"]


def test_contar_intentos_sobre_los_mismos_datos(ctx):
    c, _ = ctx
    sembrar()
    assert post(c).json()["trials"] == 1
    assert post(c, {"seed": 4}).json()["trials"] == 2  # misma base de datos y horizonte: segundo intento
    assert post(c, {"horizon": 5}).json()["trials"] == 1  # otro horizonte es otro conjunto de datos


def test_verificar_reproducibilidad_y_deteccion_de_datos_cambiados(ctx):
    c, _ = ctx
    sembrar()
    d = post(c).json()
    v = c.post(f"{BASE}/experiments/{d['id']}/verify").json()
    assert v["reproducible"] is True and v["dataset_hash_equal"] is True
    # llegan cierres nuevos DESPUÉS de la fecha del experimento: no cambian el resultado (se corta en as_of)
    last = DailyClose.objects.order_by("date").last()
    DailyClose.objects.create(
        instrument=last.instrument,
        date=last.date + timedelta(days=1),
        close=D(500),
        currency="USD",
        provider=last.provider,
    )
    assert c.post(f"{BASE}/experiments/{d['id']}/verify").json()["reproducible"] is True
    # pero si el proveedor corrige un cierre histórico, se detecta
    row = DailyClose.objects.filter(date=date(2024, 6, 1)).first()
    DailyClose.objects.filter(pk=row.pk).update(close=row.close * 2)
    v2 = c.post(f"{BASE}/experiments/{d['id']}/verify").json()
    assert v2["reproducible"] is False and v2["dataset_hash_equal"] is False and "cambiaron" in v2["reason"]


def test_experimentos_son_inmutables(ctx):
    c, _ = ctx
    sembrar()
    e = QuantExperiment.objects.get(pk=post(c).json()["id"])
    e.verdict = "PROMOVER_A_SEGUIMIENTO"
    with pytest.raises(models.ProtectedError):
        e.save()
    with pytest.raises(models.ProtectedError):
        e.delete()


def test_sin_historial_da_error_claro(ctx):
    c, _ = ctx
    r = post(c)
    assert r.status_code == 400 and "refresh_price_history" in str(r.json())
    sembrar(n_names=6, n=60)  # menos de 71 cierres: no cuenta
    assert post(c).status_code == 400


def test_universo_chico_da_datos_insuficientes_y_no_promueve(ctx):
    c, _ = ctx
    sembrar(n_names=8)
    d = post(c).json()
    assert d["verdict"] == "DATOS_INSUFICIENTES" and any(
        "Emisoras" in x for x in d["result"]["promotion"]["reasons"]
    )


@pytest.mark.parametrize(
    "body",
    [{"horizon": 7}, {"horizon": "x"}, {"models": []}, {"models": ["inventado"]}, {"models": ["ridge", "ridge"]}, {"models": "ridge"},
     {"top_k": 0}, {"top_k": 99}, {"rebalance_days": 0}, {"seed": -1}, {"capital": 5}, {"costs": {"slippage_bps": 999}},
     {"costs": {"slippage_bps": "NaN"}}, {"costs": "x"}, {"top_k": True}],
)  # fmt: skip
def test_validaciones(ctx, body):
    c, _ = ctx
    assert post(c, body).status_code == 400


def test_solo_baselines_sin_candidato_se_rechaza(ctx):
    c, _ = ctx
    sembrar()
    r = post(c, {"models": ["momentum_20", "random"]})
    assert r.status_code == 400 and "aprenda" in str(r.json())


def test_aislamiento_y_autenticacion(ctx):
    c, _ = ctx
    sembrar()
    d = post(c).json()
    otro = APIClient()
    otro.force_authenticate(get_user_model().objects.create_user("beto", password="x"))
    assert otro.get(f"{BASE}/experiments").json() == []
    assert otro.get(f"{BASE}/experiments/{d['id']}").status_code == 404
    assert otro.post(f"{BASE}/experiments/{d['id']}/verify").status_code == 404
    assert APIClient().get(f"{BASE}/experiments").status_code in (401, 403)
    assert post(otro).json()["trials"] == 1  # los intentos se cuentan por usuario


def test_limite_de_frecuencia(ctx):
    c, _ = ctx
    sembrar(n_names=6, n=100)
    codes = [
        c.post(f"{BASE}/experiments", {"models": ["ridge"], "horizon": 5}, format="json").status_code
        for _ in range(8)
    ]
    assert 429 in codes


def test_listar_experimentos_no_gasta_el_cupo_de_entrenamiento(ctx):
    c, _ = ctx
    assert all(c.get(f"{BASE}/experiments").status_code == 200 for _ in range(15))


def test_se_prefiere_un_solo_proveedor_por_instrumento(ctx):
    ids = sembrar(n_names=6, n=100)
    ins = Instrument.objects.get(pk=ids[0])
    DailyClose.objects.create(
        instrument=ins, date=date(2030, 1, 1), close=D(1), currency="USD", provider="OTRO"
    )  # un solo cierre de otro proveedor
    from .services import load_series

    series, _ = load_series()
    assert len(series[ids[0]]) == 100 and all(b["date"] != date(2030, 1, 1) for b in series[ids[0]])


def test_comando_corre_un_experimento(ctx):
    _, user = ctx
    sembrar()
    out = StringIO()
    call_command(
        "run_quant_experiment",
        "--user",
        "ana",
        "--horizon",
        "20",
        "--models",
        "reversal_5,random,ridge",
        stdout=out,
    )
    s = out.getvalue()
    assert "PROMOVER_A_SEGUIMIENTO" in s and "Rank IC" in s and "no es una recomendación" in s.lower()
    assert QuantExperiment.objects.filter(user=user).count() == 1


def test_retorno_total_usa_el_cierre_ajustado_cuando_esta_completo(ctx):
    from .services import load_series

    ids = sembrar(n_names=2, n=90)
    DailyClose.objects.filter(instrument_id=ids[0]).update(
        adj_close=models.F("close") * D("0.9")
    )  # ajuste completo
    DailyClose.objects.filter(instrument_id=ids[1]).update(adj_close=models.F("close") * D("0.9"))
    DailyClose.objects.filter(instrument_id=ids[1], date=date(2024, 1, 5)).update(
        adj_close=None
    )  # incompleto
    series, _ = load_series()
    a, b = series[ids[0]][10], series[ids[1]][10]
    assert a["adjusted"] is True and a["close"] == pytest.approx(a["raw_close"] * 0.9)
    assert (
        b["adjusted"] is False and b["close"] == b["raw_close"]
    )  # no se mezclan bases dentro de un instrumento


def test_el_resultado_declara_la_base_de_retorno(ctx):
    c, _ = ctx
    sembrar()
    sin = post(c).json()["result"]["meta"]
    assert sin["return_basis"] == {"adjusted": 0, "raw": 22} and any(
        "sin dividendos" in x for x in sin["caveats"]
    )
    DailyClose.objects.update(adj_close=models.F("close"))
    con = post(c, {"seed": 9}).json()["result"]["meta"]
    assert con["return_basis"] == {"adjusted": 22, "raw": 0} and any(
        "dividendos reinvertidos" in x for x in con["caveats"]
    )


def test_los_bloques_walk_forward_se_piden_por_api(ctx):
    c, _ = ctx
    sembrar()
    d = post(c, {"folds": 3}).json()
    assert (
        d["result"]["meta"]["split"]["mode"] == "walk_forward"
        and len(d["result"]["meta"]["split"]["folds"]) == 3
    )
    assert post(c, {"folds": 99}).status_code == 400 and post(c, {"folds": 0}).status_code == 400
    assert c.get(f"{BASE}/overview").json()["default_folds"] == 4
