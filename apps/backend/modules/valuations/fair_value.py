"""ASI Fair Value v1 (plan 5.6): capa de selección y contraste de metodologías. Propuesta del proyecto,
no un modelo publicado ni validado. No promedia a ciegas: muestra cada método, descarta los inaplicables
con explicación, y solo da un valor central cuando los modelos son compatibles entre sí."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from . import engine
from .engine import ValuationError

MODEL_VERSION = "valuation-v1"
COMPATIBLE_RATIO = Decimal("1.5")  # máx/mín entre métodos para hablar de un valor central
STALE_MONTHS = 15
DISCLAIMER = (
    "Escenarios ilustrativos condicionados a los supuestos; no es predicción ni asesoría de inversión."
)

CLASSES = {"STOCK", "ADR", "ETF", "CRYPTO"}
SECTORS = {"non_financial", "bank"}

# (clase, sector) -> métodos permitidos y motivo cuando no
DCF_BLOCK = {
    "bank": "Un DCF por FCFF/WACC industrial no aplica a bancos: usa DDM, P/B o P/E.",
    "ETF": "Un ETF no es una empresa: no se valora con DCF; valora sus componentes o revisa NAV y tracking.",
    "CRYPTO": "Una criptomoneda no tiene flujos atribuibles: un DCF empresarial no aplica.",
}
DDM_BLOCK = {
    "ETF": "El DDM no aplica a un ETF como si fuera una empresa.",
    "CRYPTO": "Una criptomoneda no paga dividendos.",
}
REL_BLOCK_ALL = {
    "ETF": "Los múltiplos de empresa no aplican a un ETF.",
    "CRYPTO": "Los múltiplos de empresa no aplican a una criptomoneda.",
}
BANK_ALLOWED_REL = {"pe", "p_b"}


def D(value, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValuationError(f"{field}: valor requerido.")
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise ValuationError(f"{field}: número inválido ({value!r}).") from None
    if not d.is_finite():
        raise ValuationError(f"{field}: número inválido ({value!r}).")
    return d


def _dec_list(values, field: str) -> list[Decimal]:
    if not isinstance(values, list) or not values:
        raise ValuationError(f"{field}: debe ser una lista no vacía.")
    return [D(v, field) for v in values]


def parse_dcf(block: dict) -> dict:
    growth = block.get("growth")
    return {
        "fcff_base": D(block.get("fcff_base"), "fcff_base"),
        "growth": _dec_list(growth, "growth") if isinstance(growth, list) else D(growth, "growth"),
        "years": int(D(block.get("years"), "years")),
        "wacc": D(block.get("wacc"), "wacc"),
        "terminal_growth": D(block.get("terminal_growth"), "terminal_growth"),
        "net_debt": D(block.get("net_debt", 0), "net_debt"),
        "diluted_shares": D(block.get("diluted_shares"), "diluted_shares"),
        "other_claims": D(block.get("other_claims", 0), "other_claims"),
        "non_operating_assets": D(block.get("non_operating_assets", 0), "non_operating_assets"),
    }


def plain(obj):
    """Decimal -> texto con 6 decimales; fechas -> ISO. Para JSON estable y sin pérdida de precisión de float."""
    if isinstance(obj, Decimal):
        return format(obj.quantize(Decimal("0.000001")), "f")
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [plain(v) for v in obj]
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    return obj


def _blocked(reason: str, method: str) -> dict:
    return {"method": method, "applicable": False, "reason": reason, "warnings": []}


def run_dcf(block: dict, asset_class: str, sector: str) -> dict:
    if reason := DCF_BLOCK.get(sector) or DCF_BLOCK.get(asset_class):
        return _blocked(reason, "dcf")
    params = parse_dcf(block)
    base = engine.dcf(**params)
    out = {"method": "dcf", **base}
    if not base["applicable"]:
        return out
    out["sensitivity"] = engine.sensitivity_wacc_g(params)
    scenarios = {"base": base}
    for name in ("bear", "bull"):
        over = block.get("scenarios", {}).get(name)
        if over:
            merged = {**block, **over}
            scenarios[name] = engine.dcf(**parse_dcf(merged))
    out["scenarios"] = {
        k: v.get("value_per_share") if v["applicable"] else None for k, v in scenarios.items()
    }
    if asset_class == "ADR":
        out["warnings"] = [
            *out["warnings"],
            "ADR: verifica el ratio ADR/acción ordinaria, la moneda funcional y el tipo de cambio.",
        ]
    return out


def run_ddm(block: dict, asset_class: str) -> dict:
    if reason := DDM_BLOCK.get(asset_class):
        return _blocked(reason, "ddm")
    r = D(block.get("cost_of_equity"), "cost_of_equity")
    if block.get("mode", "gordon") == "two_stage":
        res = engine.ddm_two_stage(
            D(block.get("d0"), "d0"), D(block.get("g_high"), "g_high"), int(D(block.get("years_high"), "years_high")),
            D(block.get("g_stable"), "g_stable"), r,
        )  # fmt: skip
    else:
        res = engine.ddm_gordon(D(block.get("d1"), "d1"), r, D(block.get("growth"), "growth"))
    return {"method": "ddm", **res}


def run_relative(blocks, asset_class: str, sector: str) -> list[dict]:
    out = []
    for b in blocks:
        method = str(b.get("method", ""))
        if reason := REL_BLOCK_ALL.get(asset_class):
            out.append(_blocked(reason, f"relative:{method}"))
            continue
        if sector == "bank" and method not in BANK_ALLOWED_REL:
            out.append(
                _blocked(
                    "Para bancos usa P/E o P/B; los múltiplos basados en EV/ventas no aplican.",
                    f"relative:{method}",
                )
            )
            continue
        res = engine.relative_value(
            method, D(b.get("metric"), "metric"), _dec_list(b.get("multiples"), "multiples"),
            D(b.get("net_debt", 0), "net_debt"),
            D(b["diluted_shares"], "diluted_shares") if b.get("diluted_shares") is not None else None,
        )  # fmt: skip
        out.append({"method": f"relative:{method}", **res})
    return out


def asi_fair_value_v1(inputs: dict) -> dict:
    asset_class = str(inputs.get("instrument_class", "")).upper()
    sector = str(inputs.get("sector_kind", "non_financial")).lower()
    if asset_class not in CLASSES:
        raise ValuationError(f"instrument_class debe ser uno de {sorted(CLASSES)}.")
    if sector not in SECTORS:
        raise ValuationError(f"sector_kind debe ser uno de {sorted(SECTORS)}.")
    price = D(inputs.get("market_price"), "market_price")
    if price <= 0:
        raise ValuationError("market_price debe ser mayor que cero.")
    currency = str(inputs.get("currency", "USD")).upper()

    methods, warnings = [], []
    if inputs.get("dcf") is not None:
        methods.append(run_dcf(inputs["dcf"], asset_class, sector))
    if inputs.get("ddm") is not None:
        methods.append(run_ddm(inputs["ddm"], asset_class))
    if inputs.get("relative"):
        methods.extend(run_relative(inputs["relative"], asset_class, sector))
    if not methods:
        raise ValuationError("Incluye al menos un bloque de supuestos: dcf, ddm o relative.")

    applicable = [m for m in methods if m.get("applicable")]
    values = [m["value_per_share"] for m in applicable]
    result = {
        "instrument_class": asset_class,
        "sector_kind": sector,
        "currency": currency,
        "market_price": price,
        "evaluable": bool(applicable),
        "methods": methods,
        "assumptions_version": MODEL_VERSION,
        "source_documents": list(inputs.get("source_documents") or []),
        "disclaimer": DISCLAIMER,
    }

    dq = inputs.get("financials_as_of")
    if dq:
        try:
            financial_date = date.fromisoformat(str(dq))
        except ValueError:
            raise ValuationError("financials_as_of debe ser una fecha ISO (YYYY-MM-DD).") from None
        if financial_date > date.today():
            raise ValuationError("financials_as_of no puede ser una fecha futura.")
        months = (date.today() - financial_date).days / 30.4
        if months > STALE_MONTHS:
            warnings.append(
                f"Los estados financieros tienen {months:.0f} meses: datos posiblemente desactualizados."
            )
    else:
        warnings.append("No se indicó la fecha de los estados financieros: no se puede evaluar su vigencia.")

    if not applicable:
        warnings.append("No evaluable: ningún método aplica a este instrumento con los datos dados.")
        result.update(valuation_range=None, central_value=None, compatible=None, margin_of_safety_base=None)
        result["warnings"] = warnings
        return result

    low, high = min(values), max(values)
    ratio = high / low if low > 0 else None
    compatible = len(values) < 2 or (ratio is not None and ratio <= COMPATIBLE_RATIO)
    sorted_vals = sorted(values)
    mid = len(sorted_vals) // 2
    median = sorted_vals[mid] if len(sorted_vals) % 2 else (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
    central = median if compatible else None
    if len(values) == 1:
        warnings.append("Un solo método: no hay contraste contra otro enfoque.")
    if not compatible:
        warnings.append(
            "Los métodos difieren mucho entre sí (máx/mín > 1.5): no se calcula un valor central; "
            "revisa los supuestos de cada uno."
        )
    result.update(
        valuation_range={"low": low, "high": high},
        central_value=central,
        compatible=compatible,
        discrepancy_ratio=ratio,
    )
    if central is None:
        result["margin_of_safety_base"], result["upside_to_value"] = None, None
    else:
        m = engine.margin_of_safety(central, price)
        result["margin_of_safety_base"], result["upside_to_value"] = (
            m["margin_of_safety"],
            m["upside_to_value"],
        )

    dcf_m = next((m for m in applicable if m["method"] == "dcf"), None)
    if dcf_m:
        result["scenario_range"] = dcf_m.get("scenarios")
        params = parse_dcf(inputs["dcf"])
        params.pop("growth")
        result["reverse_dcf"] = engine.reverse_dcf(price, params)
    for m in applicable:
        warnings.extend(f"{m['method']}: {w}" for w in m.get("warnings", []))
    result["warnings"] = warnings
    return result
