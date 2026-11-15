"""Calculadoras de rendimiento (funciones puras, Decimal).

Aclaración permanente (plan 7.4): una proyección es un ESCENARIO condicionado a supuestos; no es un
pronóstico calibrado ni una probabilidad. Las funciones devuelven datos y advertencias.
"""

from datetime import date
from decimal import Decimal, localcontext

ZERO = Decimal(0)
ONE = Decimal(1)
MAX_YEARS = 50
PREC = 40
SCENARIO_NOTE = "Escenario condicionado a tus supuestos; no es una predicción ni una probabilidad."


class CalcError(ValueError):
    """Entradas inválidas; el mensaje se muestra tal cual al usuario."""


def _pow(base: Decimal, exponent: Decimal) -> Decimal:
    """base^exponent para exponente real y base > 0 (exp/ln con precisión alta)."""
    if base <= 0:
        raise CalcError("La base de la potencia debe ser positiva.")
    with localcontext() as ctx:
        ctx.prec = PREC
        if exponent == exponent.to_integral_value():
            return base ** int(exponent)
        return (base.ln() * exponent).exp()


def _years(value: Decimal, name="Años") -> Decimal:
    if not (0 < value <= MAX_YEARS):
        raise CalcError(f"{name} debe ser mayor que 0 y no exceder {MAX_YEARS}.")
    return value


def cagr(initial: Decimal, final: Decimal, years: Decimal) -> dict:
    """CAGR = (Vf/Vi)^(1/n) − 1, SIN aportaciones ni retiros intermedios (plan 7.1)."""
    if initial <= 0 or final <= 0:
        raise CalcError("Los valores inicial y final deben ser mayores que cero.")
    _years(years)
    rate = _pow(final / initial, ONE / years) - 1
    return {
        "cagr": rate,
        "total_return": final / initial - 1,
        "years": years,
        "warnings": ["Válido solo sin depósitos ni retiros intermedios; con flujos usa XIRR."],
    }


def cagr_between_dates(initial: Decimal, final: Decimal, start: date, end: date) -> dict:
    days = (end - start).days
    if days <= 0:
        raise CalcError("La fecha final debe ser posterior a la inicial.")
    out = cagr(initial, final, Decimal(days) / Decimal(365))
    out["day_count"] = "días/365"
    return out


def real_return(nominal: Decimal, inflation: Decimal) -> dict:
    """r_real = (1 + r_nominal)/(1 + inflación) − 1; ambas tasas deben ser del mismo periodo (plan 7.3)."""
    if inflation <= -1 or nominal <= -1:
        raise CalcError("Las tasas deben ser mayores que -100%.")
    return {
        "real_return": (1 + nominal) / (1 + inflation) - 1,
        "warnings": ["Usa tasas del mismo periodo (anual con anual) y la inflación de la moneda correcta."],
    }


def fx_adjusted_return(local_return: Decimal, fx_start: Decimal, fx_end: Decimal) -> dict:
    """Retorno en MXN de un activo en USD: (1+r_usd)·(fx_fin/fx_ini) − 1. fx = MXN por USD (plan 7.6, 14.4)."""
    if fx_start <= 0 or fx_end <= 0 or local_return <= -1:
        raise CalcError("Tipos de cambio positivos y retorno mayor que -100% requeridos.")
    fx_change = fx_end / fx_start - 1
    return {
        "return_in_mxn": (1 + local_return) * (1 + fx_change) - 1,
        "fx_component": fx_change,
        "asset_component": local_return,
        "warnings": ["El componente cambiario no es variación del precio del activo."],
    }


def twr(subperiods: list[dict]) -> dict:
    """Rendimiento ponderado por tiempo: encadena subperiodos neutralizando flujos externos (plan 7.6).
    Cada subperiodo: begin_value, end_value y flow (aportación + / retiro −) hecho al INICIO del subperiodo."""
    if not subperiods:
        raise CalcError("Indica al menos un subperiodo.")
    growth, detail = ONE, []
    for i, sp in enumerate(subperiods, start=1):
        begin, end, flow = sp["begin_value"], sp["end_value"], sp.get("flow", ZERO)
        base = begin + flow
        if base <= 0 or end < 0:
            raise CalcError(f"Subperiodo {i}: el valor base debe ser positivo y el final no negativo.")
        r = end / base - 1
        growth *= 1 + r
        detail.append({"period": i, "return": r})
    return {
        "twr": growth - 1,
        "subperiods": detail,
        "warnings": ["No es comparable con un XIRR (pondera por dinero)."],
    }


def irr(flows: list[tuple[Decimal, Decimal]]) -> Decimal | None:
    """Tasa anual r con Σ cf/(1+r)^t = 0, t en años. None sin cambio de signo (no hay solución)."""
    if len(flows) < 2:
        return None

    def npv(r: Decimal) -> Decimal:
        return sum((cf / _pow(1 + r, t) for t, cf in flows), ZERO)

    lo, hi = Decimal("-0.9999"), Decimal(100)
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo == 0:
        return lo
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < Decimal("1e-15") or (hi - lo) < Decimal("1e-14"):
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def dividend_total_return(
    shares: Decimal,
    start_price: Decimal,
    end_price: Decimal,
    dividends: list[dict],
    withholding: Decimal = ZERO,
) -> dict:
    """Retorno por precio, total bruto y total neto con REINVERSIÓN de dividendos (plan 7.2).
    Cada dividendo: per_share y reinvest_price (precio al que se compran más títulos ese día), en orden cronológico.
    Sin reinversión se acumula el efectivo. `withholding` es la tasa retenida (0–1)."""
    if shares <= 0 or start_price <= 0 or end_price <= 0:
        raise CalcError("Títulos y precios deben ser mayores que cero.")
    if not (ZERO <= withholding < ONE):
        raise CalcError("La retención debe estar entre 0 y 1 (p. ej. 0.30 para 30%).")
    initial = shares * start_price

    def run(rate: Decimal, reinvest: bool) -> tuple[Decimal, Decimal]:
        held, cash = shares, ZERO
        for d in dividends:
            if d["per_share"] < 0 or d["reinvest_price"] <= 0:
                raise CalcError("Cada dividendo requiere importe ≥ 0 y precio de reinversión > 0.")
            received = held * d["per_share"] * (1 - rate)
            if reinvest:
                held += received / d["reinvest_price"]
            else:
                cash += received
        return held, cash

    held_g, _ = run(ZERO, True)
    held_n, _ = run(withholding, True)
    _, cash_g = run(ZERO, False)
    return {
        "price_return": end_price / start_price - 1,
        "total_return_cash_gross": (shares * end_price + cash_g) / initial - 1,
        "total_return_reinvested_gross": held_g * end_price / initial - 1,
        "total_return_reinvested_net": held_n * end_price / initial - 1,
        "final_shares_net": held_n,
        "warnings": [
            "Distinto de sumar dividendos al precio final: la reinversión compra más títulos en su fecha."
        ],
    }


def project(
    buy_price: Decimal,
    fundamental_initial: Decimal,
    growth: Decimal,
    years: Decimal,
    exit_multiple: Decimal,
    dilution: Decimal = ZERO,
    dividends_per_share: list[Decimal] | None = None,
) -> dict:
    """CAGR proyectado (plan 7.4): fundamental_final = fundamental·(1+g)^n; precio = fundamental_final·múltiplo;
    CAGR del precio = (precio_futuro/precio_compra)^(1/n) − 1. `dilution` es la dilución neta anual de acciones.
    Si hay dividendos por acción (uno por año), también se calcula la TIR total (precio + dividendos)."""
    if buy_price <= 0 or fundamental_initial <= 0 or exit_multiple <= 0:
        raise CalcError(
            "Precio de compra, fundamental inicial y múltiplo de salida deben ser mayores que cero."
        )
    _years(years)
    if not (Decimal("-0.9") < growth < Decimal(5)):
        raise CalcError("El crecimiento anual debe estar entre -90% y 500%.")
    if not (ZERO <= dilution < ONE):
        raise CalcError("La dilución anual debe estar entre 0 y 1.")
    effective_growth = (1 + growth) * (1 - dilution) - 1
    fundamental_final = fundamental_initial * _pow(1 + effective_growth, years)
    price_future = fundamental_final * exit_multiple
    entry_multiple = buy_price / fundamental_initial
    out = {
        "effective_growth": effective_growth,
        "fundamental_final": fundamental_final,
        "price_future": price_future,
        "entry_multiple": entry_multiple,
        "exit_multiple": exit_multiple,
        "multiple_change": exit_multiple / entry_multiple - 1,
        "cagr_price": _pow(price_future / buy_price, ONE / years) - 1,
        "scenario_note": SCENARIO_NOTE,
        "warnings": [],
    }
    if exit_multiple < entry_multiple:
        out["warnings"].append(
            "El múltiplo de salida es menor que el de entrada: compresión que resta rendimiento."
        )
    if exit_multiple > entry_multiple * Decimal("1.25"):
        out["warnings"].append(
            "El múltiplo de salida supone una expansión de más de 25%: revisa que sea razonable."
        )
    if dilution > 0:
        out["warnings"].append("La dilución reduce el crecimiento por acción.")
    if dividends_per_share is not None:
        if years != years.to_integral_value() or len(dividends_per_share) != int(years):
            raise CalcError("Con dividendos, los años deben ser enteros y debe haber un dividendo por año.")
        flows = [(ZERO, -buy_price)]
        flows += [(Decimal(t), d) for t, d in enumerate(dividends_per_share, start=1)]
        flows[-1] = (flows[-1][0], flows[-1][1] + price_future)
        rate = irr(flows)
        out["total_irr"] = rate
        if rate is None:
            out["warnings"].append("La TIR total no tiene solución con estos flujos.")
    return out


def compare_scenarios(base: dict, scenarios: dict[str, dict]) -> dict:
    """Corre la proyección con el caso base y sus variaciones (bear/bull u otras). Reporta cada una por separado:
    no pondera ni mezcla escenarios (no son probabilidades)."""
    results = {"base": project(**base)}
    for name, over in scenarios.items():
        results[name] = project(**{**base, **over})
    return {
        "scenarios": results,
        "cagr_price": {k: v["cagr_price"] for k, v in results.items()},
        "scenario_note": SCENARIO_NOTE,
    }


def max_buy_price(
    future_value: Decimal,
    years: Decimal,
    target_return: Decimal,
    dividends_per_share: list[Decimal] | None = None,
    estimated_value: Decimal | None = None,
    margin_of_safety: Decimal | None = None,
) -> dict:
    """Precio máximo de compra (plan 7.5): P = FV/(1+r)^n, y con dividendos cada flujo se descuenta en su fecha:
    P = Σ div_t/(1+r)^t + FV/(1+r)^n. Además, por margen de seguridad: P = V·(1−MoS).
    El precio conservador exige AMBOS (el menor)."""
    if future_value <= 0:
        raise CalcError("El valor futuro debe ser mayor que cero.")
    _years(years)
    if not (Decimal("-0.5") < target_return < Decimal(5)):
        raise CalcError("La rentabilidad objetivo debe estar entre -50% y 500%.")
    by_return = future_value / _pow(1 + target_return, years)
    if dividends_per_share:
        if years != years.to_integral_value() or len(dividends_per_share) != int(years):
            raise CalcError("Con dividendos, los años deben ser enteros y debe haber un dividendo por año.")
        by_return += sum(
            (d / _pow(1 + target_return, Decimal(t)) for t, d in enumerate(dividends_per_share, start=1)),
            ZERO,
        )
    out = {"max_price_by_return": by_return, "scenario_note": SCENARIO_NOTE, "warnings": []}
    if (estimated_value is None) != (margin_of_safety is None):
        raise CalcError("Para el precio por margen de seguridad indica el valor estimado y el margen.")
    if estimated_value is not None:
        if estimated_value <= 0 or not (ZERO <= margin_of_safety < ONE):
            raise CalcError("Valor estimado > 0 y margen de seguridad entre 0 y 1 (p. ej. 0.25).")
        by_mos = estimated_value * (1 - margin_of_safety)
        out["max_price_by_margin_of_safety"] = by_mos
        out["conservative_max_price"] = min(by_return, by_mos)
    else:
        out["warnings"].append(
            "Sin margen de seguridad: solo se muestra el precio por rentabilidad objetivo."
        )
    return out
