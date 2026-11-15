"""Screener de acciones potencialmente infravaloradas (plan 6).

Prioriza empresas para ESTUDIO mediante filtros cuantitativos; NO produce recomendaciones de compra.
Reglas: un dato faltante nunca aprueba ni suma cero: el filtro queda «sin dato» y la empresa se excluye
con el motivo. El ASI Rating son dimensiones visibles, sin puntaje agregado (se diseñará tras validarlo con
datos históricos). Cada resultado conserva de dónde salió cada dato para poder reproducirlo.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.pricing.services import latest_price
from modules.valuations.fair_value import plain
from modules.valuations.models import Valuation

from . import piotroski
from .services import latest_years

MODEL_VERSION = "screener-v1"
STALE_MONTHS = 15
STALE_PRICE_DAYS = 7
KEY_FIELDS = ["revenue", "gross_profit", "net_income", "cfo", "total_assets", "current_assets",
              "current_liabilities", "long_term_debt", "shares_outstanding"]  # fmt: skip
CONSENSUS_NOTE = (
    "Consenso de analistas deshabilitado: no hay un proveedor con licencia conectado. "
    "Los objetivos publicados por terceros pueden requerir licencia de datos."
)


class ScreenError(ValueError):
    """Parámetros inválidos; el mensaje se muestra tal cual."""


def _dec(params: dict, key: str, low=None, high=None):
    v = params.get(key)
    if v is None or v == "":
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise ScreenError(f"{key}: número inválido ({v!r}).") from None
    if not d.is_finite() or (low is not None and d < low) or (high is not None and d > high):
        raise ScreenError(f"{key}: fuera de rango.")
    return d


def parse_params(params: dict) -> dict:
    if not isinstance(params, dict):
        raise ScreenError("Envía los filtros como objeto JSON.")
    fscore = params.get("min_fscore")
    if fscore is not None:
        try:
            fscore = int(fscore)
        except (TypeError, ValueError):
            raise ScreenError("min_fscore: entero de 0 a 9.") from None
        if not 0 <= fscore <= 9:
            raise ScreenError("min_fscore: entero de 0 a 9.")
    as_of = params.get("as_of")
    if as_of:
        try:
            as_of = date.fromisoformat(str(as_of))
        except ValueError:
            raise ScreenError("as_of: fecha ISO (AAAA-MM-DD).") from None
    return {
        "min_fscore": fscore,
        "allow_partial_fscore": bool(params.get("allow_partial_fscore", False)),
        "min_margin_of_safety": _dec(params, "min_margin_of_safety", Decimal(-1), Decimal(1)),
        "require_positive_fcf": bool(params.get("require_positive_fcf", False)),
        "max_debt_to_assets": _dec(params, "max_debt_to_assets", Decimal(0), Decimal(5)),
        "min_revenue_growth": _dec(params, "min_revenue_growth", Decimal(-1), Decimal(20)),
        "require_fresh_data": bool(params.get("require_fresh_data", False)),
        "as_of": as_of or None,
    }


def _months_between(a: date, b: date) -> Decimal:
    return Decimal((b - a).days) / Decimal("30.4")


def _ratio(n, d):
    return None if n is None or d in (None, 0) else Decimal(n) / Decimal(d)


def _as_dict(fy) -> dict:
    return {f: getattr(fy, f) for f in ["net_income", "cfo", "total_assets", "long_term_debt", "current_assets",
            "current_liabilities", "shares_outstanding", "gross_profit", "revenue"]}  # fmt: skip


def _valuation_for(user, instrument, price):
    """Última valoración guardada del usuario con valor central; el margen se recalcula con el precio ACTUAL."""
    v = Valuation.objects.filter(user=user, instrument=instrument).order_by("-created_at", "-id").first()
    if v is None:
        return None
    central = v.output.get("central_value")
    if central is None:
        return {
            "id": v.id,
            "central_value": None,
            "margin_of_safety": None,
            "note": "La valoración no dio valor central.",
        }
    central = Decimal(central)
    margin = None if price is None or central <= 0 else (central - price) / central
    return {
        "id": v.id,
        "central_value": central,
        "margin_of_safety": margin,
        "created_at": v.created_at,
        "note": None,
    }


def _rating(label, basis):
    return {"label": label, "basis": basis}


def _ratings(fscore, margin, growth, debt_ratio, current_ratio, coverage, fin_months, price_age_days):
    if margin is None:
        valuation = _rating("No evaluable", "Sin valoración guardada o sin precio.")
    elif margin >= Decimal("0.25"):
        valuation = _rating("Atractiva", f"Margen de seguridad {margin:.1%}.")
    elif margin >= 0:
        valuation = _rating("Neutral", f"Margen de seguridad {margin:.1%}.")
    else:
        valuation = _rating("Cara", f"El precio supera el valor estimado ({margin:.1%}).")

    if fscore["status"] == "no_aplica":
        quality = _rating("No aplica", fscore["note"])
    elif fscore["evaluable_criteria"] < 6:
        quality = _rating("No evaluable", f"Solo {fscore['evaluable_criteria']} de 9 criterios evaluables.")
    else:
        s = fscore["score"]
        quality = _rating(
            "Alta" if s >= 7 else ("Media" if s >= 4 else "Baja"),
            f"F-Score {s}/{fscore['evaluable_criteria']}.",
        )

    if growth is None:
        grow = _rating("No evaluable", "Sin dos años consecutivos de ventas.")
    else:
        grow = _rating(
            "Alto" if growth > Decimal("0.10") else ("Moderado" if growth >= 0 else "Negativo"),
            f"Ventas {growth:+.1%} anual.",
        )

    if debt_ratio is None and current_ratio is None:
        risk = _rating("No evaluable", "Sin datos de deuda ni de liquidez.")
    elif (debt_ratio is not None and debt_ratio > Decimal("0.6")) or (
        current_ratio is not None and current_ratio < 1
    ):
        risk = _rating("Alto", "Deuda/activos > 60% o razón corriente < 1.")
    elif debt_ratio is not None and debt_ratio > Decimal("0.4"):
        risk = _rating("Medio", "Deuda/activos entre 40% y 60%.")
    else:
        risk = _rating("Bajo", "Deuda y liquidez dentro de rangos holgados.")

    fresh = fin_months is not None and fin_months <= STALE_MONTHS
    price_ok = price_age_days is not None and price_age_days <= STALE_PRICE_DAYS
    if coverage >= Decimal("0.8") and fresh and price_ok:
        dq = _rating("Buena", "Cobertura alta, estados vigentes y precio reciente.")
    elif coverage >= Decimal("0.5"):
        dq = _rating("Parcial", "Faltan datos o están desactualizados.")
    else:
        dq = _rating("Pobre", "Cobertura baja de datos.")
    return {
        "valuation": valuation,
        "financial_quality": quality,
        "growth": grow,
        "risk": risk,
        "data_quality": dq,
    }


def _check(name, passed, detail):
    return {"filter": name, "passed": passed, "detail": detail}


def _evaluate_instrument(user, instrument, f, today):
    years = latest_years(instrument, as_of=f["as_of"])
    if not years:
        return None
    cur, prev = years[0], (years[1] if len(years) > 1 else None)
    if instrument.sector_kind == "bank":
        fscore = piotroski.not_applicable("Sector financiero: el F-Score original lo excluye.")
    else:
        fscore = piotroski.evaluate(
            _as_dict(cur),
            _as_dict(prev) if prev else None,
            cur.fiscal_year,
            prev.fiscal_year if prev else None,
        )

    mp = latest_price(instrument)
    price = mp.price if mp else None
    price_age = (today - mp.observed_at.date()).days if mp else None
    valuation = _valuation_for(user, instrument, price)
    margin = valuation["margin_of_safety"] if valuation else None
    fcf = cur.free_cash_flow
    debt_ratio = _ratio(
        cur.total_debt if cur.total_debt is not None else cur.long_term_debt, cur.total_assets
    )
    current_ratio = _ratio(cur.current_assets, cur.current_liabilities)
    consecutive = prev is not None and prev.fiscal_year == cur.fiscal_year - 1
    growth = (
        _ratio(cur.revenue - prev.revenue, prev.revenue)
        if consecutive and cur.revenue is not None and prev.revenue
        else None
    )
    anchor = cur.published_at or cur.period_end
    fin_months = _months_between(anchor, today)
    coverage = Decimal(sum(1 for k in KEY_FIELDS if getattr(cur, k) is not None)) / Decimal(len(KEY_FIELDS))

    checks = []
    if f["min_fscore"] is not None:
        if fscore["status"] == "no_aplica":
            checks.append(_check("min_fscore", None, "F-Score no aplica a este sector."))
        elif fscore["status"] == "no_evaluable" or (
            fscore["status"] == "parcial" and not f["allow_partial_fscore"]
        ):
            checks.append(
                _check(
                    "min_fscore",
                    None,
                    f"F-Score {fscore['status']} ({fscore['evaluable_criteria']}/9 criterios).",
                )
            )
        else:
            checks.append(
                _check(
                    "min_fscore",
                    fscore["score"] >= f["min_fscore"],
                    f"F-Score {fscore['score']} (mínimo {f['min_fscore']}).",
                )
            )
    if f["min_margin_of_safety"] is not None:
        checks.append(_check("min_margin_of_safety", None if margin is None else margin >= f["min_margin_of_safety"],
                             "Sin valoración guardada o sin precio." if margin is None else f"Margen {margin:.1%}."))  # fmt: skip
    if f["require_positive_fcf"]:
        checks.append(_check("require_positive_fcf", None if fcf is None else fcf > 0, "Sin flujo operativo o capex." if fcf is None else f"FCF {fcf}."))  # fmt: skip
    if f["max_debt_to_assets"] is not None:
        checks.append(_check("max_debt_to_assets", None if debt_ratio is None else debt_ratio <= f["max_debt_to_assets"],
                             "Sin datos de deuda." if debt_ratio is None else f"Deuda/activos {debt_ratio:.1%}."))  # fmt: skip
    if f["min_revenue_growth"] is not None:
        checks.append(_check("min_revenue_growth", None if growth is None else growth >= f["min_revenue_growth"],
                             "Sin dos años consecutivos." if growth is None else f"Ventas {growth:+.1%}."))  # fmt: skip
    if f["require_fresh_data"]:
        checks.append(
            _check(
                "require_fresh_data", fin_months <= STALE_MONTHS, f"Estados de hace {fin_months:.0f} meses."
            )
        )

    failed = [c["filter"] for c in checks if c["passed"] is False]
    missing = [c["filter"] for c in checks if c["passed"] is None]
    return {
        "instrument": instrument.id,
        "ticker": next(
            iter(instrument.identifiers.filter(kind="TICKER").values_list("value", flat=True)),
            instrument.name,
        ),
        "name": instrument.name,
        "sector_kind": instrument.sector_kind,
        "passed": not failed and not missing,
        "failed_filters": failed,
        "missing_data_filters": missing,
        "checks": checks,
        "fscore": fscore,
        "metrics": {
            "free_cash_flow": fcf,
            "debt_to_assets": debt_ratio,
            "current_ratio": current_ratio,
            "revenue_growth": growth,
            "margin_of_safety": margin,
            "price": price,
        },
        "ratings": _ratings(
            fscore, margin, growth, debt_ratio, current_ratio, coverage, fin_months, price_age
        ),
        "data_refs": {
            "fiscal_year": cur.fiscal_year,
            "version": cur.version,
            "published_at": cur.published_at,
            "source": cur.source,
            "price": {"provider": mp.provider, "observed_at": mp.observed_at} if mp else None,
            "valuation_id": valuation["id"] if valuation else None,
        },
    }


def run_screen(user, raw_params: dict) -> dict:
    f = parse_params(raw_params)
    today = f["as_of"] or date.today()
    universe = (
        Instrument.objects.filter(asset_class__in=["STOCK", "ADR"], research_only=False)
        .prefetch_related("identifiers")
        .order_by("id")
    )
    rows, no_data = [], []
    for ins in universe:
        row = _evaluate_instrument(user, ins, f, today)
        if row is None:
            tickers = InstrumentIdentifier.objects.filter(instrument=ins, kind="TICKER").values_list(
                "value", flat=True
            )
            no_data.append(next(iter(tickers), ins.name))
        else:
            rows.append(row)
    candidates = sorted(
        (r for r in rows if r["passed"]),
        key=lambda r: (
            r["metrics"]["margin_of_safety"] is None,
            -(r["metrics"]["margin_of_safety"] or 0),
            -(r["fscore"]["score"] or 0),
        ),
    )
    params_out = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in f.items()}
    return plain(
        {
            "model_version": MODEL_VERSION,
            "params": params_out,
            "coverage": {
                "universe": universe.count(),
                "with_financials": len(rows),
                "passed": len(candidates),
                "without_financials": no_data,
            },
            "candidates": [r["ticker"] for r in candidates],
            "rows": rows,
            "consensus": {"enabled": False, "reason": CONSENSUS_NOTE},
            "disclaimer": "Lista para INVESTIGAR; no es una recomendación de compra ni asesoría de inversión.",
        }
    )
