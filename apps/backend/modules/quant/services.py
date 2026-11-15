"""Carga de datos desde la base y ciclo de vida de los experimentos (crear y verificar reproducibilidad)."""

from collections import defaultdict
from datetime import date

from modules.instruments.models import InstrumentIdentifier
from modules.pricing.history_models import DailyClose

from .features import MIN_HISTORY
from .models import QuantExperiment
from .runner import QUANT_VERSION, panel_hash, run_experiment

MIN_BARS = MIN_HISTORY + 10


def load_series(as_of: date | None = None, instrument_ids: list[int] | None = None) -> tuple[dict, dict]:
    """Velas por instrumento (proveedor con más datos, para no mezclar ajustes distintos), hasta `as_of`.
    Devuelve (series, tickers). Solo instrumentos con historial suficiente."""
    qs = DailyClose.objects.order_by("instrument_id", "date")
    if instrument_ids is not None:
        qs = qs.filter(instrument_id__in=instrument_ids)
    if as_of is not None:
        qs = qs.filter(date__lte=as_of)
    by_inst: dict = defaultdict(lambda: defaultdict(list))
    for r in qs:
        by_inst[r.instrument_id][r.provider].append(r)
    series = {}
    for inst, providers in by_inst.items():
        rows = providers[max(providers, key=lambda p: len(providers[p]))]
        if len(rows) >= MIN_BARS:
            series[inst] = [{"date": r.date, "close": float(r.close), "volume": r.volume} for r in rows]
    tickers = dict(
        InstrumentIdentifier.objects.filter(kind="TICKER", instrument_id__in=list(series)).values_list(
            "instrument_id", "value"
        )
    )
    return series, tickers


def create_experiment(user, name: str, config: dict) -> QuantExperiment:
    series, tickers = load_series(instrument_ids=config.get("instrument_ids"))
    if not series:
        raise ValueError(
            f"Ningún instrumento tiene historial suficiente (≥ {MIN_BARS} cierres). Corre refresh_price_history con rango 2y o 5y."
        )
    as_of = max(b["date"] for bars in series.values() for b in bars)
    cfg = {**config, "as_of": as_of.isoformat(), "instrument_ids": sorted(series)}
    prior = QuantExperiment.objects.filter(user=user, dataset_hash=panel_hash(series, cfg)).count()
    result = run_experiment(series, cfg, trials=prior + 1)
    result["meta"]["tickers"] = {str(i): tickers.get(i, str(i)) for i in series}
    return QuantExperiment.objects.create(
        user=user, name=name, horizon=result["meta"]["horizon"], config=cfg, dataset_hash=result["meta"]["dataset_hash"],
        result_hash=result["result_hash"], quant_version=QUANT_VERSION, candidate=result["candidate"],
        verdict=result["promotion"]["verdict"], trials=prior + 1, result=result,
    )  # fmt: skip


def verify(exp: QuantExperiment) -> dict:
    """Vuelve a correr el experimento con los mismos datos (hasta su `as_of`), configuración y semilla, y compara huellas."""
    cfg = exp.config
    series, _ = load_series(date.fromisoformat(cfg["as_of"]), cfg["instrument_ids"])
    if not series:
        return {"reproducible": False, "reason": "Ya no hay datos para esos instrumentos y fecha."}
    new = run_experiment(series, cfg, trials=exp.trials)
    same_data = new["meta"]["dataset_hash"] == exp.dataset_hash
    ok = same_data and new["result_hash"] == exp.result_hash
    return {
        "reproducible": ok, "dataset_hash_equal": same_data, "stored_result_hash": exp.result_hash, "new_result_hash": new["result_hash"],
        "reason": "Mismo resultado." if ok else ("Los datos históricos guardados cambiaron (el proveedor corrigió o se borraron cierres)." if not same_data else "Mismo dato, distinto resultado: revisar versiones de librerías."),
    }  # fmt: skip
