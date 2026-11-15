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
from .splits import segment, temporal_split

QUANT_VERSION = "1.0.0"
HORIZONS = (5, 20)
LEARNED = ("ridge", "lightgbm")
MODEL_LABELS = {
    "ridge": "Regresión ridge",
    "lightgbm": "LightGBM",
    **{k: v[0] for k, v in scorers.BASELINES.items()},
    "random": "Aleatorio (control)",
}
DEFAULT_MODELS = [*scorers.BASELINES, "random", *LEARNED]


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
            "models": {k: {"valid": m["valid"], "test": m["test"]} for k, m in sorted(result["models"].items()) if "valid" in m}}  # fmt: skip
    return hashlib.sha256(
        json.dumps(_round(core), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _by_date(rows, scores):
    out = {}
    for r, s in zip(rows, scores, strict=True):
        out.setdefault(r["date"], {})[r["inst"]] = s
    return out


def run_experiment(
    series: dict, config: dict, trials: int = 1, criteria: promotion.Criteria | None = None
) -> dict:
    """`series`: {instrumento: velas ascendentes (date, close, volume)}. `config`: horizon, delay, models, seed, top_k,
    rebalance_days, capital, costs (bps), train/valid (proporciones)."""
    horizon = int(config.get("horizon", 20))
    if horizon not in HORIZONS:
        raise ExperimentError(f"El horizonte debe ser uno de {list(HORIZONS)} sesiones.")
    delay, seed = int(config.get("delay", 1)), int(config.get("seed", 0))
    models = config.get("models") or DEFAULT_MODELS
    unknown = [m for m in models if m not in MODEL_LABELS]
    if unknown:
        raise ExperimentError(f"Modelo desconocido: {unknown[0]}.")
    try:
        panel = build_panel(series, horizon, delay, min_names=int(config.get("min_names", 5)))
        labelled = [
            d for d in panel["dates"] if any(r["y"] is not None for r in panel["rows"] if r["date"] == d)
        ]
        split = temporal_split(
            labelled, horizon, delay, float(config.get("train", 0.6)), float(config.get("valid", 0.2))
        )
    except (DatasetError, ValueError) as exc:
        raise ExperimentError(str(exc)) from exc
    rows = panel["rows"]
    seg = {k: segment(rows, split[k]) for k in ("train", "valid", "test")}
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
    out_models, scores = {}, {}  # scores[name] = {"valid": [...], "test": [...], "live": [...]}

    for name in models:
        if name in scorers.BASELINES or name == "random":
            scores[name] = {
                k: scorers.baseline_scores(name, s, seed)
                for k, s in (*seg.items(), ("live", live))
                if k != "train"
            }
            out_models[name] = {"label": MODEL_LABELS[name], "kind": "baseline"}
        elif name == "ridge":
            best = None
            for lam in scorers.RIDGE_GRID:
                m = scorers.fit_ridge(seg["train"], lam)
                v = summarize(ic_by_date(seg["valid"], scorers.ridge_scores(m, seg["valid"])), horizon)[
                    "rank_ic"
                ]["mean"]
                if best is None or (v if v is not None else -9) > best[0]:
                    best = (v if v is not None else -9, m)
            m = best[1]
            scores[name] = {
                k: scorers.ridge_scores(m, s)
                for k, s in (("valid", seg["valid"]), ("test", seg["test"]), ("live", live))
            }
            out_models[name] = {
                "label": MODEL_LABELS[name],
                "kind": "learned",
                "lambda": m["lambda"],
                "weights": m["weights"],
            }
        elif name == "lightgbm":
            try:
                r = worker.lightgbm_scores(
                    seg["train"],
                    seg["valid"],
                    {"valid": seg["valid"], "test": seg["test"], "live": live},
                    seed,
                )
            except worker.WorkerUnavailable as exc:
                out_models[name] = {"label": MODEL_LABELS[name], "kind": "learned", "unavailable": str(exc)}
                continue
            scores[name] = {k: r["predictions"].get(k, []) for k in ("valid", "test", "live")}
            out_models[name] = {"label": MODEL_LABELS[name], "kind": "learned", "best_iteration": r["best_iteration"],
                                "feature_importance_gain": r["feature_importance_gain"], "versions": r["versions"]}  # fmt: skip

    for name, sc in scores.items():
        out_models[name]["valid"] = summarize(ic_by_date(seg["valid"], sc["valid"]), horizon)
        out_models[name]["test"] = summarize(ic_by_date(seg["test"], sc["test"]), horizon)
        try:
            bt = run_backtest(series, _by_date(seg["test"], sc["test"]), split["test"], bt_cfg)
            out_models[name]["backtest"] = bt
        except ValueError as exc:
            out_models[name]["backtest"] = {"error": str(exc)}

    learned = [n for n in LEARNED if n in scores]
    if not learned:
        raise ExperimentError(
            "Incluye al menos un modelo que aprenda (ridge o lightgbm): hay que tener un candidato que evaluar."
        )
    cand = max(
        learned,
        key=lambda n: (
            out_models[n]["valid"]["rank_ic"]["mean"]
            if out_models[n]["valid"]["rank_ic"]["mean"] is not None
            else -9
        ),
    )
    baselines = [
        n for n in scores if n in scorers.BASELINES and out_models[n]["test"]["rank_ic"]["mean"] is not None
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
    )
    last_scores = sorted(
        zip([r["inst"] for r in live], scores[cand]["live"], strict=True), key=lambda t: -t[1]
    )
    result = {
        "meta": {
            "quant_version": QUANT_VERSION, "dataset_hash": dataset_hash(panel), "features": FEATURES, "horizon": horizon, "delay": delay, "seed": seed,
            "rows": len(rows), "dates": len(panel["dates"]), "dropped_dates": len(panel["dropped_dates"]), "universe": universe,
            "split": {k: [str(split[k][0]), str(split[k][-1]), len(split[k])] for k in ("train", "valid", "test")} | {"gap_sessions": split["gap"]},
            "live_date": str(live_date), "config": {**{k: v for k, v in config.items()}, "backtest": asdict(bt_cfg)},
            "caveats": [
                "El universo es el de instrumentos con historial guardado hoy: hay sesgo de supervivencia no corregido.",
                "Cierres ajustados por splits pero sin dividendos reinvertidos; proveedor gratuito no oficial.",
                "Una sola partición temporal: un buen resultado puede deberse al periodo, no a la señal.",
            ],
        },
        "models": out_models,
        "candidate": cand,
        "best_baseline": best_base,
        "promotion": promo,
        "latest_scores": [{"instrument": i, "score": round(s, 6)} for i, s in last_scores],
    }  # fmt: skip
    result["result_hash"] = result_hash(result)
    return result
