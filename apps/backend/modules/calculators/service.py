"""Despacho único de calculadoras: lo usan la API REST y la herramienta MCP (sin duplicar lógica).

Las tasas se reciben como decimales ("0.10" = 10%). Todo número llega como texto o número JSON y se
convierte con Decimal (nunca float)."""

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation

from modules.valuations.fair_value import plain

from . import engine
from .engine import CalcError

MAX_LIST = 60
MODES = ("historical", "projected", "real", "fx", "twr", "dividends")


def dec(value, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise CalcError(f"{field}: valor requerido.")
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise CalcError(f"{field}: número inválido ({value!r}).") from None
    if not d.is_finite():
        raise CalcError(f"{field}: número inválido ({value!r}).")
    return d


def _opt(payload: dict, key: str, default=None):
    return dec(payload[key], key) if payload.get(key) is not None else default


def _list(payload: dict, key: str, required=False) -> list | None:
    v = payload.get(key)
    if v is None:
        if required:
            raise CalcError(f"{key}: lista requerida.")
        return None
    if not isinstance(v, list) or len(v) > MAX_LIST or (required and not v):
        raise CalcError(f"{key}: debe ser una lista de 1 a {MAX_LIST} elementos.")
    return v


def _date(raw, field: str) -> date:
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise CalcError(f"{field}: fecha ISO inválida (AAAA-MM-DD).") from None


def _projection_args(p: dict) -> dict:
    divs = _list(p, "dividends_per_share")
    return {
        "buy_price": dec(p.get("buy_price"), "buy_price"),
        "fundamental_initial": dec(p.get("fundamental_initial"), "fundamental_initial"),
        "growth": dec(p.get("growth"), "growth"),
        "years": dec(p.get("years"), "years"),
        "exit_multiple": dec(p.get("exit_multiple"), "exit_multiple"),
        "dilution": _opt(p, "dilution", Decimal(0)),
        "dividends_per_share": [dec(d, "dividends_per_share") for d in divs] if divs is not None else None,
    }


def calculate(mode: str, p: dict) -> dict:
    """Devuelve {'mode', 'inputs', 'result'} con todo serializado (Decimal -> texto de 6 decimales)."""
    if not isinstance(p, dict):
        raise CalcError("Envía los parámetros como objeto JSON.")
    if mode == "historical":
        if p.get("start_date"):
            res = engine.cagr_between_dates(
                dec(p.get("initial"), "initial"), dec(p.get("final"), "final"),
                _date(p["start_date"], "start_date"), _date(p.get("end_date"), "end_date"),
            )  # fmt: skip
        else:
            res = engine.cagr(dec(p.get("initial"), "initial"), dec(p.get("final"), "final"), dec(p.get("years"), "years"))
    elif mode == "projected":
        args = _projection_args(p)
        scenarios = p.get("scenarios")
        if scenarios:
            if not isinstance(scenarios, dict) or len(scenarios) > 6:
                raise CalcError("scenarios: objeto con hasta 6 escenarios (p. ej. bear, bull).")
            overrides = {}
            for name, over in scenarios.items():
                if not isinstance(over, dict):
                    raise CalcError(f"scenarios.{name}: debe ser un objeto con los supuestos que cambian.")
                overrides[str(name)[:20]] = _projection_args({**p, **over})
            res = engine.compare_scenarios(args, overrides)
        else:
            res = engine.project(**args)
    elif mode == "real":
        res = engine.real_return(dec(p.get("nominal"), "nominal"), dec(p.get("inflation"), "inflation"))
    elif mode == "fx":
        res = engine.fx_adjusted_return(dec(p.get("local_return"), "local_return"), dec(p.get("fx_start"), "fx_start"), dec(p.get("fx_end"), "fx_end"))
    elif mode == "twr":
        subs = _list(p, "subperiods", required=True)
        res = engine.twr(
            [
                {"begin_value": dec(s.get("begin_value"), "begin_value"), "end_value": dec(s.get("end_value"), "end_value"),
                 "flow": dec(s.get("flow", 0), "flow")}
                for s in subs if isinstance(s, dict)
            ]
        )
    elif mode == "dividends":
        divs = _list(p, "dividends", required=True)
        res = engine.dividend_total_return(
            dec(p.get("shares"), "shares"), dec(p.get("start_price"), "start_price"), dec(p.get("end_price"), "end_price"),
            [{"per_share": dec(d.get("per_share"), "per_share"), "reinvest_price": dec(d.get("reinvest_price"), "reinvest_price")} for d in divs if isinstance(d, dict)],
            _opt(p, "withholding", Decimal(0)),
        )  # fmt: skip
    else:
        raise CalcError(f"mode debe ser uno de {', '.join(MODES)}.")
    return {"mode": mode, "inputs": p, "result": plain(res)}


def target_buy_price(p: dict) -> dict:
    if not isinstance(p, dict):
        raise CalcError("Envía los parámetros como objeto JSON.")
    divs = _list(p, "dividends_per_share")
    res = engine.max_buy_price(
        dec(p.get("future_value"), "future_value"), dec(p.get("years"), "years"), dec(p.get("target_return"), "target_return"),
        [dec(d, "dividends_per_share") for d in divs] if divs is not None else None,
        _opt(p, "estimated_value"), _opt(p, "margin_of_safety"),
    )  # fmt: skip
    return {"mode": "target_buy_price", "inputs": p, "result": plain(res)}


def to_csv(data: dict) -> str:
    """Exporta la hipótesis (entradas + resultados) como filas sección,campo,valor, aplanando anidados."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["seccion", "campo", "valor"])

    def walk(section, prefix, value):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(section, f"{prefix}.{k}" if prefix else str(k), v)
        elif isinstance(value, list):
            for i, v in enumerate(value, start=1):
                walk(section, f"{prefix}[{i}]", v)
        else:
            text = "" if value is None else str(value)
            w.writerow([section, prefix, ("'" + text) if text[:1] in "=+-@" and not _is_number(text) else text])

    w.writerow(["modo", "", data["mode"]])
    walk("entradas", "", data["inputs"])
    walk("resultados", "", data["result"])
    return out.getvalue()


def _is_number(text: str) -> bool:
    try:
        Decimal(text)
        return True
    except InvalidOperation:
        return False
