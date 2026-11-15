"""Piotroski F-Score (0–9) con evidencia por criterio (plan 6.2).

Definiciones APLICADAS (registradas porque el original admite variantes):
- ROA = utilidad neta / activos totales al cierre del año (el original usa activos al inicio).
- Apalancamiento = deuda de largo plazo / activos totales (el original usa la variación sobre activos promedio).
- Razón corriente = activos circulantes / pasivos circulantes.
- Sin emisión de acciones = acciones diluidas al cierre ≤ las del año anterior (no ajusta splits ni eventos corporativos).
Si falta un dato, el criterio es «no evaluable» (None), NUNCA un cero que sugiera malos fundamentos.
Un 8/9 no implica infravaloración ni un 2/9 una caída segura: es un filtro de investigación.
"""

from decimal import Decimal

CRITERIA = [
    ("roa_positive", "ROA positivo"),
    ("cfo_positive", "Flujo operativo positivo"),
    ("roa_improved", "ROA mejor que el año anterior"),
    ("accruals", "Flujo operativo superior a la utilidad neta"),
    ("leverage_down", "Menor deuda de largo plazo sobre activos"),
    ("liquidity_up", "Mayor razón corriente"),
    ("no_dilution", "Sin emisión de acciones"),
    ("gross_margin_up", "Mayor margen bruto"),
    ("asset_turnover_up", "Mayor rotación de activos"),
]
PREV_NEEDED = {
    "roa_improved",
    "leverage_down",
    "liquidity_up",
    "no_dilution",
    "gross_margin_up",
    "asset_turnover_up",
}


def _ratio(num, den):
    if num is None or den is None or den == 0:
        return None
    return Decimal(num) / Decimal(den)


def _metrics(y: dict) -> dict:
    return {
        "roa": _ratio(y.get("net_income"), y.get("total_assets")),
        "ltd_to_assets": _ratio(y.get("long_term_debt"), y.get("total_assets")),
        "current_ratio": _ratio(y.get("current_assets"), y.get("current_liabilities")),
        "gross_margin": _ratio(y.get("gross_profit"), y.get("revenue")),
        "asset_turnover": _ratio(y.get("revenue"), y.get("total_assets")),
    }


def _cmp(a, b, op):
    """Compara si ambos existen; None si falta alguno (no evaluable)."""
    if a is None or b is None:
        return None
    return op(a, b)


def evaluate(
    cur: dict, prev: dict | None, fiscal_year: int | None = None, prev_year: int | None = None
) -> dict:
    """cur/prev: dicts con net_income, cfo, total_assets, long_term_debt, current_assets, current_liabilities,
    shares_outstanding, gross_profit, revenue (Decimal o None). prev puede ser None."""
    if (
        prev is not None
        and fiscal_year is not None
        and prev_year is not None
        and prev_year != fiscal_year - 1
    ):
        prev = None  # años no consecutivos: la comparación año contra año no es válida
        gap = True
    else:
        gap = False
    mc = _metrics(cur)
    mp = _metrics(prev) if prev else {k: None for k in mc}
    cfo, ni = cur.get("cfo"), cur.get("net_income")
    shares_c, shares_p = cur.get("shares_outstanding"), (prev or {}).get("shares_outstanding")

    raw = {
        "roa_positive": (_cmp(mc["roa"], Decimal(0), lambda a, b: a > b), {"roa": mc["roa"]}),
        "cfo_positive": (_cmp(cfo, Decimal(0), lambda a, b: a > b), {"cfo": cfo}),
        "roa_improved": (
            _cmp(mc["roa"], mp["roa"], lambda a, b: a > b),
            {"roa": mc["roa"], "roa_prev": mp["roa"]},
        ),
        "accruals": (_cmp(cfo, ni, lambda a, b: a > b), {"cfo": cfo, "net_income": ni}),
        "leverage_down": (
            _cmp(mc["ltd_to_assets"], mp["ltd_to_assets"], lambda a, b: a < b),
            {"ltd_to_assets": mc["ltd_to_assets"], "ltd_to_assets_prev": mp["ltd_to_assets"]},
        ),
        "liquidity_up": (
            _cmp(mc["current_ratio"], mp["current_ratio"], lambda a, b: a > b),
            {"current_ratio": mc["current_ratio"], "current_ratio_prev": mp["current_ratio"]},
        ),
        "no_dilution": (
            _cmp(shares_c, shares_p, lambda a, b: a <= b),
            {"shares": shares_c, "shares_prev": shares_p},
        ),
        "gross_margin_up": (
            _cmp(mc["gross_margin"], mp["gross_margin"], lambda a, b: a > b),
            {"gross_margin": mc["gross_margin"], "gross_margin_prev": mp["gross_margin"]},
        ),
        "asset_turnover_up": (
            _cmp(mc["asset_turnover"], mp["asset_turnover"], lambda a, b: a > b),
            {"asset_turnover": mc["asset_turnover"], "asset_turnover_prev": mp["asset_turnover"]},
        ),
    }
    criteria = []
    for key, label in CRITERIA:
        passed, evidence = raw[key]
        reason = None
        if passed is None:
            if key in PREV_NEEDED and prev is None:
                reason = (
                    "No hay datos del año anterior consecutivo." if gap else "No hay datos del año anterior."
                )
            else:
                reason = "Faltan datos para calcularlo."
        criteria.append(
            {
                "id": key,
                "label": label,
                "passed": passed,
                "points": None if passed is None else int(passed),
                "evidence": evidence,
                "reason": reason,
            }
        )
    evaluable = sum(1 for c in criteria if c["passed"] is not None)
    score = sum(c["points"] for c in criteria if c["points"] is not None)
    status = "completo" if evaluable == 9 else ("parcial" if evaluable else "no_evaluable")
    return {
        "score": score if evaluable else None,
        "evaluable_criteria": evaluable,
        "status": status,
        "criteria": criteria,
        "note": "Filtro de investigación, no una recomendación; las definiciones aplicadas están documentadas.",
    }


def not_applicable(reason: str) -> dict:
    return {"score": None, "evaluable_criteria": 0, "status": "no_aplica", "criteria": [], "note": reason}
