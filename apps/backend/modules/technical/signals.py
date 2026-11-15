"""Señales técnicas AUDITABLES (plan 9.1 y 10): una señal es un EVENTO con condiciones exactas, no una predicción.

Cada señal informa su regla, los valores que la sustentan y la fecha de la última vela; con las mismas velas y
parámetros el resultado es idéntico. Si faltan velas o campos (p. ej. volumen), la señal es «no evaluable»
(fired = None) con el motivo, nunca falsa por omisión."""

from . import indicators as ind

DEFAULTS = {"breakout_days": 20, "ma_days": 20, "rsi_days": 14, "rsi_oversold": 30.0, "rvol_days": 20,
            "rvol_threshold": 2.0, "ema_fast": 50, "ema_slow": 200, "atr_days": 14, "stop_atr": 2.0, "reward_r": 2.0,
            "level_atr_distance": 1.0}  # fmt: skip
NOTE = (
    "Los indicadores derivan de los mismos precios: no son pruebas independientes entre sí. "
    "Una señal es un evento reproducible, no una predicción ni una recomendación."
)


class SignalError(ValueError):
    pass


def resolve_params(overrides: dict | None) -> dict:
    p = dict(DEFAULTS)
    for k, v in (overrides or {}).items():
        if k not in DEFAULTS:
            raise SignalError(f"Parámetro desconocido: {k}.")
        try:
            p[k] = float(v) if isinstance(DEFAULTS[k], float) else int(v)
        except (TypeError, ValueError):
            raise SignalError(f"{k}: número inválido.") from None
    for k in ("breakout_days", "ma_days", "rsi_days", "rvol_days", "ema_fast", "ema_slow", "atr_days"):
        if not 2 <= p[k] <= 400:
            raise SignalError(f"{k} debe estar entre 2 y 400.")
    if p["ema_fast"] >= p["ema_slow"]:
        raise SignalError("ema_fast debe ser menor que ema_slow.")
    if not 0 < p["rsi_oversold"] < 100:
        raise SignalError("rsi_oversold debe estar entre 0 y 100.")
    if p["stop_atr"] <= 0 or p["reward_r"] <= 0 or p["rvol_threshold"] <= 0 or p["level_atr_distance"] <= 0:
        raise SignalError("stop_atr, reward_r, rvol_threshold y level_atr_distance deben ser positivos.")
    return p


def _col(bars, key):
    vals = [b.get(key) for b in bars]
    return None if any(v is None for v in vals) else [float(v) for v in vals]


def _signal(sid, name, rule, fired, conditions, reason=None):
    return {"id": sid, "name": name, "rule": rule, "fired": fired, "conditions": conditions, "reason": reason}


def _unavailable(sid, name, rule, reason):
    return _signal(sid, name, rule, None, [], reason)


def analyze(bars: list[dict], overrides: dict | None = None) -> dict:
    """`bars`: velas diarias ascendentes {date, open, high, low, close, volume} (valores None permitidos)."""
    p = resolve_params(overrides)
    if len(bars) < 2:
        raise SignalError("Se necesitan al menos 2 velas.")
    closes = _col(bars, "close")
    if closes is None:
        raise SignalError("Hay velas sin cierre.")
    highs, lows, vols = _col(bars, "high"), _col(bars, "low"), _col(bars, "volume")
    n = len(closes)
    last, prev = closes[-1], closes[-2]
    signals = []

    # 1. Ruptura de máximos de N días
    rule = f"Cierre > máximo de los {p['breakout_days']} cierres anteriores."
    if n < p["breakout_days"] + 1:
        signals.append(
            _unavailable(
                "breakout", "Ruptura de máximo", rule, f"Se necesitan {p['breakout_days'] + 1} velas."
            )
        )
    else:
        ref = max(closes[-p["breakout_days"] - 1 : -1])
        signals.append(_signal("breakout", "Ruptura de máximo", rule, last > ref,
                               [{"text": "Cierre actual", "value": last}, {"text": "Máximo previo", "value": ref}]))  # fmt: skip

    # 2. Recuperación de la media móvil
    rule = f"Cierre previo < SMA({p['ma_days']}) previa y cierre actual > SMA actual."
    sma = ind.sma(closes, p["ma_days"])
    if n < p["ma_days"] + 1:
        signals.append(
            _unavailable("reclaim_ma", "Recupera la media", rule, f"Se necesitan {p['ma_days'] + 1} velas.")
        )
    else:
        signals.append(_signal("reclaim_ma", "Recupera la media", rule, prev < sma[-2] and last > sma[-1],
                               [{"text": "Cierre previo / SMA previa", "value": [prev, sma[-2]]},
                                {"text": "Cierre actual / SMA actual", "value": [last, sma[-1]]}]))  # fmt: skip

    # 3-4. Sobreventa y rebote desde sobreventa
    rsi = ind.rsi(closes, p["rsi_days"])
    rule_os = f"RSI({p['rsi_days']}) < {p['rsi_oversold']:g}."
    rule_ob = f"RSI previo < {p['rsi_oversold']:g} y cierre actual > cierre previo."
    if rsi[-1] is None or rsi[-2] is None:
        signals.append(
            _unavailable("oversold", "Sobreventa", rule_os, f"Se necesitan {p['rsi_days'] + 2} velas.")
        )
        signals.append(
            _unavailable(
                "oversold_bounce",
                "Rebote desde sobreventa",
                rule_ob,
                f"Se necesitan {p['rsi_days'] + 2} velas.",
            )
        )
    else:
        signals.append(
            _signal(
                "oversold",
                "Sobreventa",
                rule_os,
                rsi[-1] < p["rsi_oversold"],
                [{"text": "RSI", "value": rsi[-1]}],
            )
        )
        signals.append(_signal("oversold_bounce", "Rebote desde sobreventa", rule_ob, rsi[-2] < p["rsi_oversold"] and last > prev,
                               [{"text": "RSI previo", "value": rsi[-2]}, {"text": "Cierre previo / actual", "value": [prev, last]}]))  # fmt: skip

    # 5. Tendencia alcista estructural
    rule = f"Cierre > EMA({p['ema_fast']}) > EMA({p['ema_slow']})."
    ef, es = ind.ema(closes, p["ema_fast"]), ind.ema(closes, p["ema_slow"])
    if es[-1] is None:
        signals.append(
            _unavailable("trend_up", "Tendencia alcista", rule, f"Se necesitan {p['ema_slow']} velas.")
        )
    else:
        signals.append(_signal("trend_up", "Tendencia alcista", rule, last > ef[-1] > es[-1],
                               [{"text": "Cierre / EMA rápida / EMA lenta", "value": [last, ef[-1], es[-1]]}]))  # fmt: skip

    # 6. Cruce alcista del MACD
    rule = "Histograma MACD(12,26,9) previo <= 0 y actual > 0."
    hist = ind.macd(closes)["histogram"]
    if hist[-1] is None or hist[-2] is None:
        signals.append(
            _unavailable("macd_bull_cross", "Cruce alcista del MACD", rule, "Se necesitan 36 velas.")
        )
    else:
        signals.append(_signal("macd_bull_cross", "Cruce alcista del MACD", rule, hist[-2] <= 0 < hist[-1],
                               [{"text": "Histograma previo / actual", "value": [hist[-2], hist[-1]]}]))  # fmt: skip

    # 7. Volumen inusual
    rule = f"Volumen relativo (vs. {p['rvol_days']} días anteriores) >= {p['rvol_threshold']:g}."
    if vols is None:
        signals.append(_unavailable("volume_spike", "Volumen inusual", rule, "Faltan datos de volumen."))
    else:
        rv = ind.relative_volume(vols, p["rvol_days"])
        if rv[-1] is None:
            signals.append(
                _unavailable(
                    "volume_spike",
                    "Volumen inusual",
                    rule,
                    f"Se necesitan {p['rvol_days'] + 1} velas con volumen.",
                )
            )
        else:
            signals.append(
                _signal(
                    "volume_spike",
                    "Volumen inusual",
                    rule,
                    rv[-1] >= p["rvol_threshold"],
                    [{"text": "RVOL", "value": rv[-1]}],
                )
            )

    # Contexto: ATR y niveles (necesitan máximos y mínimos)
    context = {"last_close": last, "last_date": bars[-1]["date"]}
    plan = None
    if highs and lows:
        atr = ind.atr(highs, lows, closes, p["atr_days"])
        context["atr"] = atr[-1]
        levels = ind.key_levels(highs, lows, last)[:6]
        context["levels"] = levels
        if atr[-1]:
            near = [lv for lv in levels if abs(lv["price"] - last) <= p["level_atr_distance"] * atr[-1]]
            rule = f"Cierre a menos de {p['level_atr_distance']:g}×ATR de un nivel de soporte/resistencia."
            signals.append(_signal("near_level", "Cerca de un nivel", rule, bool(near),
                                   [{"text": "Niveles cercanos", "value": [(lv["kind"], round(lv["price"], 4), lv["touches"]) for lv in near]}]))  # fmt: skip
            risk = p["stop_atr"] * atr[-1]
            plan = {"side": "BUY", "entry": last, "stop": last - risk, "target": last + p["reward_r"] * risk,
                    "basis": f"Stop a {p['stop_atr']:g}×ATR y objetivo a {p['reward_r']:g}R. Aritmética de ejemplo, no una recomendación."}  # fmt: skip
        else:
            signals.append(
                _unavailable(
                    "near_level",
                    "Cerca de un nivel",
                    "Cierre cerca de un nivel.",
                    f"Se necesitan {p['atr_days']} velas.",
                )
            )
    else:
        signals.append(
            _unavailable(
                "near_level", "Cerca de un nivel", "Cierre cerca de un nivel.", "Faltan máximos y mínimos."
            )
        )
    context["rsi"] = rsi[-1]
    context["ema_fast"], context["ema_slow"], context["sma"] = ef[-1], es[-1], sma[-1]
    return {"as_of": bars[-1]["date"], "bars": n, "params": p, "context": context, "signals": signals,
            "plan_suggestion": plan, "note": NOTE}  # fmt: skip
