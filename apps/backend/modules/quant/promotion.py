"""Criterio explícito de promoción de un modelo candidato (plan, cierre de la Etapa 11).

"Promover" significa únicamente que el candidato merece seguimiento posterior (paper trading, más datos). NO es una
recomendación de inversión ni autoriza operar. El veredicto sale de reglas fijas, escritas aquí, no de un juicio ad hoc:

- Datos suficientes: fechas de prueba y tamaño del universo mínimos; si faltan, el veredicto es DATOS_INSUFICIENTES y
  no se evalúa lo demás (con poca muestra cualquier resultado es ruido).
- Capacidad: Rank IC medio en prueba, estadístico t con muestra efectiva (corrige solape de etiquetas), proporción de
  fechas con IC positivo y signo positivo en validación (estabilidad entre periodos).
- Valor frente a lo simple: supera al mejor baseline en Rank IC de prueba.
- Valor económico: rendimiento neto de costos superior al del universo en partes iguales con el mismo rebalanceo.
- Pruebas múltiples: cada experimento adicional sobre los mismos datos aumenta la probabilidad de un falso positivo;
  el umbral de t se endurece con una corrección tipo Bonferroni según los intentos previos sobre el mismo conjunto."""

from dataclasses import asdict, dataclass
from statistics import NormalDist

PROMOTE, NOT_PROMOTED, INSUFFICIENT = "PROMOVER_A_SEGUIMIENTO", "NO_PROMOVIDO", "DATOS_INSUFICIENTES"


@dataclass(frozen=True)
class Criteria:
    min_test_dates: int = 60
    min_universe: int = 20
    min_rank_ic: float = 0.02
    min_t_adjusted: float = 2.0
    min_pct_positive: float = 0.55
    min_valid_rank_ic: float = 0.0
    min_net_excess_return: float = 0.0


def required_t(base: float, trials: int) -> float:
    """t mínimo con corrección por `trials` intentos sobre los mismos datos (Bonferroni bilateral al 5 %)."""
    return max(base, NormalDist().inv_cdf(1 - 0.025 / max(trials, 1)))


def _check(id_, label, value, threshold, passed, direction=">="):
    return {
        "id": id_,
        "label": label,
        "value": value,
        "threshold": threshold,
        "direction": direction,
        "passed": bool(passed),
    }


def evaluate(
    candidate: dict,
    best_baseline: dict | None,
    n_test_dates: int,
    universe_size: int,
    excess_net: float | None,
    trials: int = 1,
    criteria: Criteria = Criteria(),
) -> dict:
    """`candidate`/`best_baseline`: {"name", "valid": resumen, "test": resumen} (resúmenes de metrics.summarize)."""
    c = criteria
    size_checks = [
        _check(
            "test_dates", "Fechas de prueba", n_test_dates, c.min_test_dates, n_test_dates >= c.min_test_dates
        ),
        _check(
            "universe",
            "Emisoras en el universo",
            universe_size,
            c.min_universe,
            universe_size >= c.min_universe,
        ),
    ]
    base = {"criteria": asdict(c), "trials_on_same_data": trials, "candidate": candidate["name"]}
    if not all(x["passed"] for x in size_checks):
        return {**base, "verdict": INSUFFICIENT, "checks": size_checks, "reasons": [
            "Muestra demasiado chica para concluir algo: ningún resultado de este experimento se puede interpretar como señal.",
            *[f"{x['label']}: {x['value']} (mínimo {x['threshold']})." for x in size_checks if not x["passed"]]]}  # fmt: skip
    t, v = candidate["test"], candidate["valid"]
    ric, t_adj, pos = t["rank_ic"]["mean"], t["rank_ic"]["t_adjusted"], t["rank_ic"]["pct_positive"]
    v_ric = v["rank_ic"]["mean"]
    base_ric = best_baseline["test"]["rank_ic"]["mean"] if best_baseline else None
    t_req = required_t(c.min_t_adjusted, trials)
    checks = size_checks + [
        _check("rank_ic", "Rank IC medio en prueba", ric, c.min_rank_ic, ric is not None and ric >= c.min_rank_ic),
        _check("t_adjusted", "t con muestra efectiva", t_adj, round(t_req, 3), t_adj is not None and t_adj >= t_req),
        _check("pct_positive", "Fechas con IC positivo", pos, c.min_pct_positive, pos is not None and pos >= c.min_pct_positive),
        _check("valid_sign", "Rank IC en validación", v_ric, c.min_valid_rank_ic, v_ric is not None and v_ric > c.min_valid_rank_ic, ">"),
        _check("beats_baseline", f"Supera al mejor baseline ({best_baseline['name'] if best_baseline else 'ninguno'})", ric, base_ric,
               ric is not None and base_ric is not None and ric > base_ric, ">"),
        _check("net_excess", "Exceso neto de costos vs universo igual", excess_net, c.min_net_excess_return,
               excess_net is not None and excess_net > c.min_net_excess_return, ">"),
    ]  # fmt: skip
    failed = [x for x in checks if not x["passed"]]
    reasons = [
        f"Falla: {x['label']} (valor {x['value']}, umbral {x['direction']} {x['threshold']})." for x in failed
    ]
    return {
        **base,
        "verdict": NOT_PROMOTED if failed else PROMOTE,
        "checks": checks,
        "reasons": reasons or ["Cumple todos los criterios: merece seguimiento, no operar."],
    }
