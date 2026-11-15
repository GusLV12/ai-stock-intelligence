"""Motor de valoración (funciones puras, Decimal, sin base de datos ni red).

Todo resultado es CONDICIONAL a los supuestos recibidos (plan 5.1): no es un precio verdadero ni una
predicción. Las funciones devuelven datos y advertencias; cuando un método no aplica, lo dicen
(`applicable: False` + razón) en vez de producir una cifra engañosa.
"""

from decimal import Decimal, localcontext

ZERO = Decimal(0)
ONE = Decimal(1)
MAX_YEARS = 30
TV_SHARE_WARNING = Decimal("0.75")
HIGH_TERMINAL_G = Decimal("0.04")  # por encima del crecimiento nominal de largo plazo típico


class ValuationError(ValueError):
    """Supuestos inválidos (p. ej. WACC <= g). Se muestra tal cual al usuario."""


def _check_rate(name: str, value: Decimal, low=Decimal("-0.5"), high=ONE):
    if not (low < value < high):
        raise ValuationError(f"{name} fuera de rango razonable ({low} a {high}).")


def _growth_path(growth, years: int) -> list[Decimal]:
    if isinstance(growth, list | tuple):
        path = [Decimal(g) for g in growth]
        if len(path) != years:
            raise ValuationError("La lista de crecimientos debe tener un valor por cada año proyectado.")
    else:
        path = [Decimal(growth)] * years
    for g in path:
        _check_rate("Crecimiento", g, Decimal("-0.9"), Decimal(5))
    return path


def dcf(
    fcff_base: Decimal,
    growth,
    years: int,
    wacc: Decimal,
    terminal_growth: Decimal,
    net_debt: Decimal,
    diluted_shares: Decimal,
    other_claims: Decimal = ZERO,
    non_operating_assets: Decimal = ZERO,
) -> dict:
    """Valor por acción por FCFF descontado al WACC (plan 5.2).

    EV = Σ FCFF_t/(1+WACC)^t + TV_n/(1+WACC)^n,  TV_n = FCFF_n·(1+g)/(WACC−g)
    Acción = (EV − deuda neta − otros derechos + activos no operativos) / acciones diluidas
    """
    if not 1 <= years <= MAX_YEARS:
        raise ValuationError(f"Los años proyectados deben estar entre 1 y {MAX_YEARS}.")
    _check_rate("WACC", wacc, ZERO, ONE)
    _check_rate("Crecimiento terminal", terminal_growth)
    if wacc <= terminal_growth:
        raise ValuationError("El WACC debe ser mayor que el crecimiento terminal (WACC > g).")
    if diluted_shares <= 0:
        raise ValuationError("Las acciones diluidas deben ser mayores que cero.")
    warnings = []
    if fcff_base <= 0:
        return {
            "applicable": False,
            "reason": "FCFF base no positivo: un DCF con flujos negativos no es fiable; usa otro método.",
            "warnings": [],
        }
    path = _growth_path(growth, years)
    with localcontext() as ctx:
        ctx.prec = 40
        flows, pv_sum, fcff = [], ZERO, fcff_base
        for t, g in enumerate(path, start=1):
            fcff = fcff * (1 + g)
            pv = fcff / (1 + wacc) ** t
            flows.append({"year": t, "fcff": fcff, "pv": pv})
            pv_sum += pv
        tv = fcff * (1 + terminal_growth) / (wacc - terminal_growth)
        pv_tv = tv / (1 + wacc) ** years
        ev = pv_sum + pv_tv
        equity = ev - net_debt - other_claims + non_operating_assets
        per_share = equity / diluted_shares
        tv_share = pv_tv / ev
    if tv_share > TV_SHARE_WARNING:
        warnings.append(
            f"El valor terminal pesa {tv_share * 100:.0f}% del valor de empresa: el resultado depende mucho de g y WACC."
        )
    if terminal_growth > HIGH_TERMINAL_G:
        warnings.append("El crecimiento terminal supera 4%: revisa que sea sostenible a perpetuidad.")
    if equity <= 0:
        warnings.append("El valor del capital es no positivo con estos supuestos.")
    return {
        "applicable": True,
        "value_per_share": per_share,
        "enterprise_value": ev,
        "equity_value": equity,
        "pv_explicit_flows": pv_sum,
        "pv_terminal_value": pv_tv,
        "terminal_value_share": tv_share,
        "flows": flows,
        "warnings": warnings,
    }


def sensitivity_wacc_g(params: dict, wacc_steps=(-2, -1, 0, 1, 2), g_steps=(-1, -0.5, 0, 0.5, 1)) -> dict:
    """Matriz WACC × g (pasos en puntos porcentuales). Las combinaciones inválidas quedan en null."""
    base_w, base_g = params["wacc"], params["terminal_growth"]
    waccs = [base_w + Decimal(str(s)) / 100 for s in wacc_steps]
    gs = [base_g + Decimal(str(s)) / 100 for s in g_steps]
    grid = []
    for w in waccs:
        row = []
        for g in gs:
            try:
                res = dcf(**{**params, "wacc": w, "terminal_growth": g})
                row.append(res.get("value_per_share") if res["applicable"] else None)
            except ValuationError:
                row.append(None)
        grid.append(row)
    return {"wacc": waccs, "terminal_growth": gs, "value_per_share": grid}


def ddm_gordon(d1: Decimal, cost_of_equity: Decimal, growth: Decimal) -> dict:
    """V0 = D1/(r − g) (plan 5.3)."""
    if d1 <= 0:
        return {
            "applicable": False,
            "reason": "Sin dividendo esperado positivo: el DDM no aplica.",
            "warnings": [],
        }
    _check_rate("Costo de capital", cost_of_equity, ZERO, ONE)
    _check_rate("Crecimiento del dividendo", growth)
    if cost_of_equity <= growth:
        raise ValuationError("El costo de capital debe ser mayor que el crecimiento del dividendo (r > g).")
    warnings = []
    if growth > HIGH_TERMINAL_G:
        warnings.append("Un crecimiento perpetuo del dividendo superior a 4% rara vez es sostenible.")
    return {"applicable": True, "value_per_share": d1 / (cost_of_equity - growth), "warnings": warnings}


def ddm_two_stage(
    d0: Decimal, g_high: Decimal, years_high: int, g_stable: Decimal, cost_of_equity: Decimal
) -> dict:
    """DDM de dos etapas: crecimiento alto durante `years_high` años y luego estable a perpetuidad."""
    if d0 <= 0:
        return {"applicable": False, "reason": "Sin dividendo positivo: el DDM no aplica.", "warnings": []}
    if not 1 <= years_high <= MAX_YEARS:
        raise ValuationError(f"Los años de la etapa alta deben estar entre 1 y {MAX_YEARS}.")
    _check_rate("Costo de capital", cost_of_equity, ZERO, ONE)
    _check_rate("Crecimiento etapa alta", g_high)
    _check_rate("Crecimiento estable", g_stable)
    if cost_of_equity <= g_stable:
        raise ValuationError("El costo de capital debe ser mayor que el crecimiento estable (r > g).")
    with localcontext() as ctx:
        ctx.prec = 40
        pv, div = ZERO, d0
        for t in range(1, years_high + 1):
            div = div * (1 + g_high)
            pv += div / (1 + cost_of_equity) ** t
        tv = div * (1 + g_stable) / (cost_of_equity - g_stable)
        pv_tv = tv / (1 + cost_of_equity) ** years_high
    return {"applicable": True, "value_per_share": pv + pv_tv, "pv_terminal_value": pv_tv, "warnings": []}


RELATIVE_METHODS = {
    "pe": "P/E",
    "p_fcf": "P/FCF",
    "p_s": "P/S",
    "ev_ebitda": "EV/EBITDA",
    "p_b": "P/B",
}


def _quantile(sorted_vals: list[Decimal], q: Decimal) -> Decimal:
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def relative_value(
    method: str,
    metric: Decimal,
    multiples: list[Decimal],
    net_debt: Decimal = ZERO,
    diluted_shares: Decimal | None = None,
) -> dict:
    """Valor por múltiplos de comparables (plan 5.4). Para ev_ebitda, `metric` es el EBITDA TOTAL y se
    convierte a valor por acción con la deuda neta y las acciones diluidas; en el resto, `metric` es por acción.
    Los múltiplos ≤ 0 se descartan; con EPS/FCF/EBITDA ≤ 0 el método no aplica."""
    if method not in RELATIVE_METHODS:
        raise ValuationError(f"Método relativo desconocido: {method}.")
    valid = sorted(m for m in multiples if m > 0)
    if not valid:
        raise ValuationError("Necesitas al menos un múltiplo comparable positivo.")
    if metric <= 0 and method != "p_b":
        return {
            "applicable": False,
            "reason": f"{RELATIVE_METHODS[method]} no aplica con una métrica no positiva (utilidad/flujo/EBITDA).",
            "warnings": [],
        }
    if metric <= 0:
        return {
            "applicable": False,
            "reason": "El valor en libros por acción no es positivo.",
            "warnings": [],
        }
    low, mid, high = (
        _quantile(valid, Decimal("0.25")),
        _quantile(valid, Decimal("0.5")),
        _quantile(valid, Decimal("0.75")),
    )

    def per_share(mult: Decimal) -> Decimal:
        if method == "ev_ebitda":
            if not diluted_shares or diluted_shares <= 0:
                raise ValuationError("EV/EBITDA requiere acciones diluidas positivas.")
            return (metric * mult - net_debt) / diluted_shares
        return metric * mult

    warnings = []
    if len(valid) < 3:
        warnings.append("Menos de 3 comparables: el rango no es representativo.")
    warnings.append("Un sector entero puede estar caro o barato: no uses los múltiplos como único método.")
    return {
        "applicable": True,
        "label": RELATIVE_METHODS[method],
        "multiple_low": low, "multiple_median": mid, "multiple_high": high,
        "value_low": per_share(low), "value_per_share": per_share(mid), "value_high": per_share(high),
        "warnings": warnings,
    }  # fmt: skip


def reverse_dcf(
    price: Decimal, params: dict, low=Decimal("-0.5"), high=ONE, tol=Decimal("0.0000001")
) -> dict:
    """Crecimiento anual constante del FCFF implícito en el precio actual (plan 5.5), por bisección.
    `params` son los de dcf() sin `growth`."""
    if price <= 0:
        raise ValuationError("El precio debe ser mayor que cero.")

    def value(g: Decimal):
        res = dcf(**{**params, "growth": g})
        return res.get("value_per_share") if res["applicable"] else None

    v_low, v_high = value(low), value(high)
    if v_low is None or v_high is None:
        return {"applicable": False, "reason": "FCFF base no positivo: no se puede resolver el DCF inverso."}
    if not (v_low <= price <= v_high):
        return {
            "applicable": True,
            "solved": False,
            "reason": "Con estos supuestos ningún crecimiento entre -50% y 100% reproduce el precio actual.",
            "value_at_low_growth": v_low,
            "value_at_high_growth": v_high,
        }
    for _ in range(200):
        mid = (low + high) / 2
        v = value(mid)
        if abs(v - price) <= tol * price or (high - low) < Decimal("1e-12"):
            return {"applicable": True, "solved": True, "implied_growth": mid, "value_at_implied_growth": v}
        if v < price:
            low = mid
        else:
            high = mid
    mid = (low + high) / 2
    return {"applicable": True, "solved": True, "implied_growth": mid, "value_at_implied_growth": value(mid)}


def margin_of_safety(value: Decimal, price: Decimal) -> dict:
    """Margen de seguridad = (V−P)/V y potencial hasta V = V/P − 1 (plan 5.1). Ninguno es ganancia asegurada."""
    if price <= 0:
        raise ValuationError("El precio debe ser mayor que cero.")
    return {
        "margin_of_safety": (value - price) / value if value > 0 else None,
        "upside_to_value": value / price - ONE,
    }
