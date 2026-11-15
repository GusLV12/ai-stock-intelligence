"""Orquesta un experimento completo, sin base de datos (función pura de las velas y la configuración):
panel → partición con purga → baselines/ridge/LightGBM → IC en validación y prueba → backtest neto de costos → promoción.

Disciplina contra el sobreajuste: el candidato se elige SOLO con la validación (entre los modelos que aprenden); la
prueba se calcula una vez y no retroalimenta ninguna decisión. Mismos datos + misma configuración + misma semilla =
mismo resultado (se verifica con `result_hash`)."""

import hashlib
import json
from dataclasses import asdict

from . import promotion, scorers, worker
from .backtest import BacktestConfig, CostModel, run_backtest
from .dataset import DatasetError, build_panel, dataset_hash
from .features import FEATURES
from .metrics import ic_by_date, summarize
from .splits import segment, temporal_split, walk_forward

QUANT_VERSION = "1.1.0"
DEFAULT_FOLDS = 4
MAX_FOLDS = 8
HORIZONS = (5, 20)
LEARNED = ("ridge", "lightgbm", "qlib_lgbm")
MODEL_LABELS = {
    "ridge": "Regresión ridge",
    "lightgbm": "LightGBM",
    "qlib_lgbm": "Qlib Alpha158 + LightGBM",
    **{k: v[0] for k, v in scorers.BASELINES.items()},
    "random": "Aleatorio (control)",
}
ALL_MODELS = [*scorers.BASELINES, "random", *LEARNED]
HEAVY_MODELS = ("qlib_lgbm",)  # lentos (varios minutos): se piden de forma explícita
DEFAULT_MODELS = [m for m in ALL_MODELS if m not in HEAVY_MODELS]


class ExperimentError(ValueError):
    pass


def _round(obj, nd=10):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_round(v, nd) for v in obj]
    return obj


def result_hash(result: dict) -> str:
    """Huella de lo esencial del resultado (excluye listas largas de curvas, que se derivan de lo demás)."""
    core = {"dataset_hash": result["meta"]["dataset_hash"], "candidate": result["candidate"], "verdict": result["promotion"]["verdict"],
            "models": {k: {"valid": m["valid"], "test": m["test"], "folds": m["folds"]} for k, m in sorted(result["models"].items()) if "valid" in m}}  # fmt: skip
    return hashlib.sha256(
        json.dumps(_round(core), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _by_date(rows, scores):
    out = {}
    for r, s in zip(rows, scores, strict=True):
        out.setdefault(r["date"], {})[r["inst"]] = s
    return out


def panel_hash(series: dict, config: dict) -> str:
    """Huella del conjunto de datos que usaría el experimento (sirve para contar intentos previos sobre los mismos datos)."""
    try:
        panel = build_panel(
            series,
            int(config.get("horizon", 20)),
            int(config.get("delay", 1)),
            int(config.get("min_names", 5)),
        )
    except DatasetError as exc:
        raise ExperimentError(str(exc)) from exc
    return dataset_hash(panel)


def _fit_models(models, train, valid, predict, seed, horizon, ctx=None):
    """Ajusta cada modelo con `train`, elige hiperparámetros con `valid` y puntúa cada conjunto de `predict`
    ({nombre: filas}). Devuelve {modelo: {"scores": {nombre: [...]}, "info": {...}}} o {"unavailable": ...}."""
    out = {}
    for name in models:
        if name in scorers.BASELINES or name == "random":
            out[name] = {
                "scores": {k: scorers.baseline_scores(name, r, seed) for k, r in predict.items()},
                "info": {"kind": "baseline"},
            }
        elif name == "ridge":
            best = None
            for lam in scorers.RIDGE_GRID:
                m = scorers.fit_ridge(train, lam)
                v = summarize(ic_by_date(valid, scorers.ridge_scores(m, valid)), horizon)["rank_ic"]["mean"]
                if best is None or (v if v is not None else -9) > best[0]:
                    best = (v if v is not None else -9, m)
            m = best[1]
            out[name] = {"scores": {k: scorers.ridge_scores(m, r) for k, r in predict.items()},
                         "info": {"kind": "learned", "lambda": m["lambda"], "weights": m["weights"]}}  # fmt: skip
        elif name == "lightgbm":
            try:
                r = worker.lightgbm_scores(train, valid, predict, seed)
            except worker.WorkerUnavailable as exc:
                out[name] = {"unavailable": str(exc)}
                continue
            out[name] = {"scores": {k: r["predictions"].get(k, []) for k in predict},
                         "info": {"kind": "learned", "best_iteration": r["best_iteration"],
                                  "feature_importance_gain": r["feature_importance_gain"], "versions": r["versions"]}}  # fmt: skip
        elif name == "qlib_lgbm":
            try:
                r = worker.qlib_scores(
                    ctx["series"], ctx["train_dates"], ctx["valid_dates"], horizon, ctx["delay"], seed
                )
            except worker.WorkerUnavailable as exc:
                out[name] = {"unavailable": str(exc)}
                continue
            sc = r["scores"]
            absent = sum(
                1 for rows in predict.values() for x in rows if (str(x["date"]), x["inst"]) not in sc
            )
            out[name] = {"scores": {k: [sc.get((str(x["date"]), x["inst"]), 0.0) for x in rows] for k, rows in predict.items()},
                         "info": {"kind": "learned", "versions": r["versions"], "alpha158_features": r["features"],
                                  "rows_without_score": absent, "bars_without_ohlc": r["bars_without_ohlc"]}}  # fmt: skip
    return out


def _range(dates):
    return [str(dates[0]), str(dates[-1]), len(dates)] if dates else None


def run_experiment(
    series: dict, config: dict, trials: int = 1, criteria: promotion.Criteria | None = None
) -> dict:
    """`series`: {instrumento: velas ascendentes (date, close, volume)}. `config`: horizon, delay, models, seed, top_k,
    rebalance_days, capital, costs (bps), folds (1 = una sola partición; ≥2 = walk-forward, por defecto 4)."""
    horizon = int(config.get("horizon", 20))
    if horizon not in HORIZONS:
        raise ExperimentError(f"El horizonte debe ser uno de {list(HORIZONS)} sesiones.")
    delay, seed = int(config.get("delay", 1)), int(config.get("seed", 0))
    n_folds = int(config.get("folds", DEFAULT_FOLDS))
    if not 1 <= n_folds <= MAX_FOLDS:
        raise ExperimentError(f"Los bloques de validación deben estar entre 1 y {MAX_FOLDS}.")
    models = config.get("models") or DEFAULT_MODELS
    unknown = [m for m in models if m not in MODEL_LABELS]
    if unknown:
        raise ExperimentError(f"Modelo desconocido: {unknown[0]}.")
    try:
        panel = build_panel(series, horizon, delay, min_names=int(config.get("min_names", 5)))
        labelled = [
            d for d in panel["dates"] if any(r["y"] is not None for r in panel["rows"] if r["date"] == d)
        ]
        if n_folds == 1:
            single = temporal_split(
                labelled, horizon, delay, float(config.get("train", 0.6)), float(config.get("valid", 0.2))
            )
            folds, live_split, gap = [single], None, single["gap"]
        else:
            wf = walk_forward(
                labelled,
                horizon,
                delay,
                n_folds,
                float(config.get("min_history", 0.4)),
                float(config.get("valid", 0.2)),
            )
            folds, live_split, gap = wf["folds"], wf["live"], wf["gap"]
    except (DatasetError, ValueError) as exc:
        raise ExperimentError(str(exc)) from exc
    rows = panel["rows"]
    live_date = panel["dates"][-1]
    live = [r for r in rows if r["date"] == live_date and r["y"] is None]
    universe = sorted({r["inst"] for r in rows})

    cm = config.get("costs", {})
    bt_cfg = BacktestConfig(
        top_k=int(config.get("top_k", 3)),
        rebalance_days=int(config.get("rebalance_days", horizon)),
        delay=delay,
        capital=float(config.get("capital", 100_000)),
        costs=CostModel(
            float(cm.get("commission_bps", 1)),
            float(cm.get("half_spread_bps", 2)),
            float(cm.get("slippage_bps", 2)),
        ),
    )
    out_models: dict = {}
    acc: dict = {}  # modelo -> ICs de validación y prueba, puntajes de prueba por fecha, resultado por bloque
    live_scores: dict = {}

    for fi, f in enumerate(folds):
        seg = {k: segment(rows, f[k]) for k in ("train", "valid", "test")}
        predict = {"valid": seg["valid"], "test": seg["test"]}
        if live_split is None:
            predict["live"] = live  # una sola partición: la misma que se evaluó
        ctx = {"series": series, "train_dates": f["train"], "valid_dates": f["valid"], "delay": delay}
        fitted = _fit_models(models, seg["train"], seg["valid"], predict, seed, horizon, ctx)
        for name, fit in fitted.items():
            if "unavailable" in fit:
                out_models[name] = {
                    "label": MODEL_LABELS[name],
                    "kind": "learned",
                    "unavailable": fit["unavailable"],
                }
                continue
            a = acc.setdefault(
                name, {"ics_valid": [], "ics_test": [], "test_scores": {}, "folds": [], "info": []}
            )
            ics_v = ic_by_date(seg["valid"], fit["scores"]["valid"])
            ics_t = ic_by_date(seg["test"], fit["scores"]["test"])
            a["ics_valid"] += ics_v
            a["ics_test"] += ics_t
            for r, sc in zip(seg["test"], fit["scores"]["test"], strict=True):
                a["test_scores"].setdefault(r["date"], {})[r["inst"]] = sc
            a["folds"].append({"fold": fi + 1, "test": _range(f["test"]), "rank_ic": summarize(ics_t, horizon)["rank_ic"]["mean"],
                               "valid_rank_ic": summarize(ics_v, horizon)["rank_ic"]["mean"]})  # fmt: skip
            a["info"].append(fit["info"])
            if live_split is None:
                live_scores[name] = fit["scores"]["live"]

    if (
        live_split is not None
    ):  # walk-forward: puntaje de la fecha más reciente con un ajuste sobre toda la historia
        lseg = {k: segment(rows, live_split[k]) for k in ("train", "valid")}
        for name, fit in _fit_models(
            list(acc), lseg["train"], lseg["valid"], {"live": live}, seed, horizon
        ).items():
            if "scores" in fit:
                live_scores[name] = fit["scores"]["live"]

    test_dates = sorted({d for f in folds for d in f["test"]})
    for name, a in acc.items():
        out_models[name] = {"label": MODEL_LABELS[name], **a["info"][-1], "valid": summarize(a["ics_valid"], horizon),
                            "test": summarize(a["ics_test"], horizon), "folds": a["folds"]}  # fmt: skip
        try:
            out_models[name]["backtest"] = run_backtest(series, a["test_scores"], test_dates, bt_cfg)
        except ValueError as exc:
            out_models[name]["backtest"] = {"error": str(exc)}

    learned = [n for n in LEARNED if n in acc]
    if not learned:
        raise ExperimentError(
            "Incluye al menos un modelo que aprenda (ridge o lightgbm): hay que tener un candidato que evaluar."
        )

    def valid_score(n):  # el candidato se elige solo con validación (promedio de los bloques)
        vs = [x["valid_rank_ic"] for x in out_models[n]["folds"] if x["valid_rank_ic"] is not None]
        return sum(vs) / len(vs) if vs else -9

    cand = max(learned, key=valid_score)
    baselines = [
        n for n in acc if n in scorers.BASELINES and out_models[n]["test"]["rank_ic"]["mean"] is not None
    ]
    best_base = max(baselines, key=lambda n: out_models[n]["test"]["rank_ic"]["mean"]) if baselines else None
    bt = out_models[cand].get("backtest", {})
    promo = promotion.evaluate(
        {"name": cand, "valid": out_models[cand]["valid"], "test": out_models[cand]["test"]},
        {"name": best_base, "test": out_models[best_base]["test"]} if best_base else None,
        n_test_dates=out_models[cand]["test"]["n_dates"],
        universe_size=len(universe),
        excess_net=bt.get("excess_vs_equal_weight"),
        trials=trials,
        criteria=criteria or promotion.Criteria(),
        fold_ics=[x["rank_ic"] for x in out_models[cand]["folds"]],
    )
    last_scores = sorted(
        zip([r["inst"] for r in live], live_scores.get(cand, []), strict=False), key=lambda t: -t[1]
    )
    adjusted = [bool(bars[0].get("adjusted")) for bars in series.values()]
    basis = {"adjusted": sum(adjusted), "raw": len(adjusted) - sum(adjusted)}
    caveats = [
        "El universo es el de instrumentos con historial guardado hoy: hay sesgo de supervivencia no corregido."
    ]
    if n_folds == 1:
        caveats.append(
            "Una sola partición temporal: un buen resultado puede deberse al periodo, no a la señal."
        )
    else:
        caveats.append(
            f"Walk-forward en {n_folds} bloques: evalúa en varios periodos, pero todos pertenecen a un mismo tramo de historia (un solo régimen de mercado)."
        )
    if basis["raw"] == 0:
        caveats.append(
            "Retornos con dividendos reinvertidos (cierre ajustado de Yahoo, fuente gratuita no oficial)."
        )
    else:
        caveats.append(
            f"{basis['raw']} de {len(adjusted)} emisoras no tienen cierre ajustado completo: se usó su cierre sin dividendos "
            "(retorno subestimado para quienes pagan dividendos). Vuelve a correr refresh_price_history."
        )
    result = {
        "meta": {
            "quant_version": QUANT_VERSION, "dataset_hash": dataset_hash(panel), "features": FEATURES, "horizon": horizon, "delay": delay, "seed": seed,
            "rows": len(rows), "dates": len(panel["dates"]), "dropped_dates": len(panel["dropped_dates"]), "universe": universe,
            "split": {"mode": "single" if n_folds == 1 else "walk_forward", "gap_sessions": gap,
                      "folds": [{"train": _range(f["train"]), "valid": _range(f["valid"]), "test": _range(f["test"])} for f in folds]},
            "live_date": str(live_date), "config": {**config, "backtest": asdict(bt_cfg)},
            "return_basis": basis, "caveats": caveats,
        },
        "models": out_models,
        "candidate": cand,
        "best_baseline": best_base,
        "promotion": promo,
        "latest_scores": [{"instrument": i, "score": round(s, 6)} for i, s in last_scores],
    }  # fmt: skip
    result["result_hash"] = result_hash(result)
    return result
