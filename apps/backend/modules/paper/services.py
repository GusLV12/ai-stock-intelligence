"""Servicios del paper trading: precios de referencia con procedencia, ejecución simulada, reproceso de órdenes
limitadas, resumen, curva de capital, métricas y propuestas a partir de un experimento del Quant Lab.

Todo el estado de la cuenta se reconstruye con `engine.replay` a partir de las órdenes ejecutadas (única fuente de
verdad). Este módulo no importa nada del libro contable real ni de ningún cliente de broker."""

import math
from datetime import UTC, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.utils import timezone

from modules.pricing.history_models import DailyClose
from modules.pricing.services import latest_price

from . import engine
from .engine import PaperError, Rules
from .models import PaperAccount, PaperOrder

LIMIT_EXPIRY_DAYS = 30
BPS = Decimal(10_000)


# ------------------------------------------------------------------------------------------------ precios de referencia
def reference_price(instrument, now: datetime) -> tuple[Decimal, dict] | None:
    """Último precio conocido (el más reciente entre precio manual/proveedor y cierre diario) con su procedencia."""
    cands = []
    mp = latest_price(instrument)
    if mp is not None:
        cands.append(
            (mp.observed_at, mp.price, mp.currency, {"kind": "MarketPrice", "provider": mp.provider})
        )
    dc = DailyClose.objects.filter(instrument=instrument).order_by("-date").first()
    if dc is not None:
        # un cierre diario se conoce al terminar la sesión: se le asigna la hora aproximada del cierre de EE. UU.
        cands.append(
            (
                datetime.combine(dc.date, time(21, 0), UTC),
                dc.close,
                dc.currency,
                {"kind": "DailyClose", "provider": dc.provider},
            )
        )
    if not cands:
        return None
    at, px, cur, src = max(cands, key=lambda c: c[0])
    age = round((now - at).total_seconds() / 86400, 2)
    return Decimal(px), {**src, "observed_at": at.isoformat(), "age_days": age, "currency": cur}


# ------------------------------------------------------------------------------------------------------------ cuenta
def fills_of(account: PaperAccount) -> list[engine.Fill]:
    rows = account.orders.filter(status=PaperOrder.Status.FILLED).order_by("decided_at", "id")
    return [
        engine.Fill(o.id, o.instrument_id, o.side, o.quantity, o.fill_price, o.fee, o.decided_at)
        for o in rows
    ]


def account_state(account: PaperAccount) -> engine.State:
    return engine.replay(account.initial_cash, fills_of(account))


def _marks(state: engine.State, now: datetime) -> dict:
    from modules.instruments.models import Instrument

    out = {}
    for inst_id in state.positions():
        ref = reference_price(Instrument.objects.get(pk=inst_id), now)
        if ref is not None:
            out[inst_id] = ref[0]
    return out


def create_account(
    user, name: str, initial_cash: Decimal, config: dict | None = None, currency: str = "USD"
) -> PaperAccount:
    if not initial_cash.is_finite() or not Decimal(100) <= initial_cash <= Decimal("1e9"):
        raise PaperError("El capital inicial debe estar entre 100 y 1,000,000,000.")
    cfg = {k: str(v) for k, v in (config or {}).items()}
    Rules.from_config(cfg)  # valida
    return PaperAccount.objects.create(
        user=user,
        name=name[:80] or "Cuenta de práctica",
        initial_cash=initial_cash,
        currency=currency,
        config=cfg,
    )


# ------------------------------------------------------------------------------------------------------- ejecución
def _reject(order: PaperOrder, reason: str, now: datetime, ref=None) -> PaperOrder:
    order.status, order.reason, order.decided_at = PaperOrder.Status.REJECTED, reason[:300], now
    if ref:
        order.reference_price, order.reference_source = ref[0], ref[1]
    order.save()
    return order


def _try_fill(order: PaperOrder, now: datetime, creating: bool) -> PaperOrder:
    account, rules = order.account, Rules.from_config(order.account.config)
    ref = reference_price(order.instrument, now)
    if ref is None:
        return _reject(order, "No hay ningún precio de este instrumento.", now) if creating else order
    price, src = ref
    if src["currency"] != account.currency:
        return _reject(
            order,
            f"El instrumento cotiza en {src['currency']} y la cuenta es en {account.currency}.",
            now,
            ref,
        )
    if src["age_days"] > rules.max_price_age_days:
        msg = f"Precio desactualizado ({src['age_days']:.1f} días; máximo {rules.max_price_age_days})."
        return _reject(order, msg, now, ref) if creating else order
    limit = order.limit_price
    if order.order_type == PaperOrder.Type.LIMIT:
        marketable = price <= limit if order.side == "BUY" else price >= limit
        if not marketable:
            return order  # sigue pendiente
    fp = engine.fill_price(price, order.side, rules.slippage_bps)
    if limit is not None:  # nunca peor que el límite del operador
        fp = min(fp, limit) if order.side == "BUY" else max(fp, limit)
    fee = engine.commission(order.quantity * fp, rules)
    state = account_state(account)
    try:
        if order.side == "BUY":
            engine.validate_buy(
                state, order.instrument_id, order.quantity, fp, fee, _marks(state, now), rules
            )
        else:
            engine.validate_sell(state, order.instrument_id, order.quantity)
    except PaperError as exc:
        return _reject(order, str(exc), now, ref)
    adverse = (fp - price) / price * BPS if order.side == "BUY" else (price - fp) / price * BPS
    order.status, order.decided_at = PaperOrder.Status.FILLED, now
    order.reference_price, order.reference_source = price, src
    order.fill_price, order.fee, order.adverse_slippage_bps = fp, fee, adverse.quantize(Decimal("0.0001"))
    order.save()
    return order


@transaction.atomic
def place_order(
    account, instrument, side, quantity, order_type="MARKET", limit_price=None, note="", source=None, now=None
) -> PaperOrder:
    """Crea y, si procede, ejecuta (simulado) una orden. Los errores de captura lanzan PaperError y no dejan rastro;
    los rechazos de negocio (sin efectivo, precio viejo…) se guardan como orden REJECTED con su motivo."""
    now = now or timezone.now()
    if side not in ("BUY", "SELL") or order_type not in ("MARKET", "LIMIT"):
        raise PaperError("Lado u orden inválidos.")
    if instrument.research_only:
        raise PaperError("Ese instrumento es solo de investigación; elige uno de tu catálogo.")
    quantity = engine.check_quantity(quantity, instrument.quantity_scale)
    if order_type == "LIMIT":
        if (
            limit_price is None
            or not limit_price.is_finite()
            or not Decimal(0) < limit_price <= Decimal("1e12")
        ):
            raise PaperError("La orden limitada necesita un precio límite positivo.")
    else:
        limit_price = None
    order = PaperOrder.objects.create(
        account=account, instrument=instrument, side=side, order_type=order_type, quantity=quantity, limit_price=limit_price,
        note=(note or "")[:300], source=source or {},
    )  # fmt: skip
    return _try_fill(order, now, creating=True)


@transaction.atomic
def process_pending(account, now=None) -> dict:
    """Reevalúa las órdenes limitadas pendientes con los precios más recientes y vence las que llevan demasiado tiempo."""
    now = now or timezone.now()
    out = {"filled": [], "expired": [], "rejected": [], "still_pending": []}
    for o in (
        account.orders.filter(status=PaperOrder.Status.PENDING)
        .select_related("instrument", "account")
        .order_by("created_at", "id")
    ):
        if now - o.created_at > timedelta(days=LIMIT_EXPIRY_DAYS):
            o.status, o.reason, o.decided_at = (
                PaperOrder.Status.EXPIRED,
                f"Venció a los {LIMIT_EXPIRY_DAYS} días.",
                now,
            )
            o.save()
            out["expired"].append(o.id)
            continue
        o = _try_fill(o, now, creating=False)
        key = {"FILLED": "filled", "REJECTED": "rejected"}.get(o.status, "still_pending")
        out[key].append(o.id)
    return out


@transaction.atomic
def cancel_order(order: PaperOrder, now=None) -> PaperOrder:
    if order.status != PaperOrder.Status.PENDING:
        raise PaperError("Solo se pueden cancelar órdenes pendientes.")
    order.status, order.decided_at, order.reason = (
        PaperOrder.Status.CANCELLED,
        now or timezone.now(),
        "Cancelada por el usuario.",
    )
    order.save()
    return order


# --------------------------------------------------------------------------------------------------------- resumen
def summary(account: PaperAccount, now=None) -> dict:
    from modules.instruments.models import Instrument, InstrumentIdentifier

    now = now or timezone.now()
    st = account_state(account)
    marks, positions = _marks(st, now), []
    tickers = dict(
        InstrumentIdentifier.objects.filter(
            kind="TICKER", instrument_id__in=list(st.positions())
        ).values_list("instrument_id", "value")
    )
    for inst_id, qty in sorted(st.positions().items()):
        px, cost = marks.get(inst_id), st.cost_basis(inst_id)
        ref = reference_price(Instrument.objects.get(pk=inst_id), now)
        positions.append({
            "instrument": inst_id, "ticker": tickers.get(inst_id, str(inst_id)), "quantity": qty, "avg_cost": cost / qty, "price": px,
            "market_value": qty * px if px is not None else None, "unrealized": qty * px - cost if px is not None else None,
            "price_source": ref[1] if ref else None,
        })  # fmt: skip
    total = engine.equity(st, marks)
    return {
        "account": {"id": account.id, "name": account.name, "currency": account.currency, "initial_cash": account.initial_cash, "config": account.config, "created_at": account.created_at},
        "cash": st.cash, "equity": total, "return_pct": (total / account.initial_cash - 1) * 100,
        "realized": st.realized, "unrealized": sum((p["unrealized"] or Decimal(0) for p in positions), Decimal(0)),
        "fees": st.fees, "positions": positions, "pending_orders": account.orders.filter(status="PENDING").count(),
        "notice": "Cuenta virtual: no hay dinero real ni órdenes reales. Precios con retraso de proveedor gratuito; la ejecución es una simulación.",
    }  # fmt: skip


def equity_curve(account: PaperAccount) -> list[dict]:
    """Capital diario (efectivo + posiciones al cierre). Antes de la primera operación es el capital inicial."""
    orders = list(account.orders.filter(status=PaperOrder.Status.FILLED).order_by("decided_at", "id"))
    start = account.created_at.date()
    curve = [{"date": start, "equity": account.initial_cash}]
    if not orders:
        return curve
    first = orders[0].decided_at.date()
    insts = {o.instrument_id for o in orders}
    closes: dict = {i: {} for i in insts}
    for r in DailyClose.objects.filter(instrument_id__in=insts, date__gte=first).order_by("provider", "date"):
        closes[r.instrument_id][r.date] = r.close
    dates = sorted({d for c in closes.values() for d in c} | {o.decided_at.date() for o in orders})
    state, pending, last = engine.State(cash=account.initial_cash), list(orders), {}
    for d in dates:
        while pending and pending[0].decided_at.date() <= d:
            o = pending.pop(0)
            engine.apply_fill(
                state,
                engine.Fill(o.id, o.instrument_id, o.side, o.quantity, o.fill_price, o.fee, o.decided_at),
            )
            last.setdefault(o.instrument_id, o.fill_price)
        for i in insts:
            if d in closes[i]:
                last[i] = closes[i][d]
        curve.append({"date": d, "equity": engine.equity(state, dict(last))})
    return curve


def metrics(account: PaperAccount, now=None) -> dict:
    curve = equity_curve(account)
    values = [float(c["equity"]) for c in curve]
    rets = [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if values[i - 1] > 0]
    peak, mdd = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    n = len(rets)
    sd = math.sqrt(sum((r - sum(rets) / n) ** 2 for r in rets) / (n - 1)) if n > 1 else None
    st = account_state(account)
    wins = [c["pnl"] for c in st.closed if c["pnl"] > 0]
    losses = [c["pnl"] for c in st.closed if c["pnl"] < 0]
    filled = list(account.orders.filter(status="FILLED"))
    slip = [o.adverse_slippage_bps for o in filled if o.adverse_slippage_bps is not None]
    traded = sum((o.quantity * o.fill_price for o in filled), Decimal(0))
    return {
        "days": len(curve), "total_return": values[-1] / values[0] - 1, "max_drawdown": mdd,
        "daily_vol": sd, "sharpe": (sum(rets) / n / sd * math.sqrt(252)) if sd and n >= 30 else None,
        "sharpe_note": None if n >= 30 else "Menos de 30 observaciones diarias: no se calcula Sharpe (sería ruido).",
        "orders": {s: account.orders.filter(status=s).count() for s in ("FILLED", "REJECTED", "PENDING", "CANCELLED", "EXPIRED")},
        "closed_trades": len(st.closed), "win_rate": len(wins) / len(st.closed) if st.closed else None,
        "avg_win": sum(wins, Decimal(0)) / len(wins) if wins else None, "avg_loss": sum(losses, Decimal(0)) / len(losses) if losses else None,
        "profit_factor": (sum(wins, Decimal(0)) / -sum(losses, Decimal(0))) if losses and wins else None,
        "realized": st.realized, "fees": st.fees, "traded_value": traded,
        "avg_adverse_slippage_bps": sum(slip, Decimal(0)) / len(slip) if slip else None,
        "sample_note": "Pocas operaciones cerradas: la tasa de acierto y el factor de beneficio no son concluyentes." if len(st.closed) < 30 else None,
    }  # fmt: skip


# ------------------------------------------------------------------------------- propuesta desde el Quant Lab
def propose_from_experiment(
    account: PaperAccount, experiment, top_k: int = 3, cash_buffer: Decimal = Decimal("0.02"), now=None
) -> dict:
    """Convierte el ranking más reciente de un experimento en órdenes PROPUESTAS (no se crean): equiponderadas entre las
    `top_k` primeras, vendiendo lo que ya no esté en el top. El usuario decide si las coloca."""
    from modules.instruments.models import Instrument, InstrumentIdentifier

    now = now or timezone.now()
    ranking = experiment.result.get("latest_scores", [])[:top_k]
    warnings = [
        "Ranking de investigación, no una recomendación: las propuestas son para practicar y medir si la señal sirve."
    ]
    if experiment.verdict != "PROMOVER_A_SEGUIMIENTO":
        warnings.append(
            f"Este experimento NO fue promovido (veredicto {experiment.verdict}): úsalo solo como ensayo, sin esperar ventaja."
        )
    st = account_state(account)
    marks = _marks(st, now)
    total = engine.equity(st, marks)
    target_value = total * (1 - cash_buffer) / max(len(ranking), 1)
    rules = Rules.from_config(account.config)
    insts = {
        i.id: i
        for i in Instrument.objects.filter(pk__in=[r["instrument"] for r in ranking] + list(st.positions()))
    }
    tickers = dict(
        InstrumentIdentifier.objects.filter(kind="TICKER", instrument_id__in=list(insts)).values_list(
            "instrument_id", "value"
        )
    )
    proposals, wanted = [], {r["instrument"] for r in ranking}
    for inst_id, qty in st.positions().items():
        if inst_id not in wanted:
            proposals.append(
                {
                    "instrument": inst_id,
                    "ticker": tickers.get(inst_id, str(inst_id)),
                    "side": "SELL",
                    "quantity": qty,
                    "reason": "Salió del top del ranking.",
                }
            )
    for r in ranking:
        ins = insts.get(r["instrument"])
        if ins is None or ins.research_only:
            warnings.append(
                f"{tickers.get(r['instrument'], r['instrument'])}: instrumento solo de investigación; no se puede practicar con él."
            )
            continue
        ref = reference_price(ins, now)
        if ref is None or ref[1]["age_days"] > rules.max_price_age_days:
            warnings.append(f"{tickers.get(ins.id, ins.id)}: sin precio reciente; no se propone.")
            continue
        px = engine.fill_price(ref[0], "BUY", rules.slippage_bps)
        step = Decimal(1).scaleb(-ins.quantity_scale)
        want = (target_value / px).quantize(step, rounding=ROUND_DOWN)
        diff = want - st.quantity(ins.id)
        if diff > 0:
            proposals.append(
                {
                    "instrument": ins.id,
                    "ticker": tickers.get(ins.id, str(ins.id)),
                    "side": "BUY",
                    "quantity": diff,
                    "reason": f"Puesto {ranking.index(r) + 1} del ranking.",
                }
            )
        elif diff < 0:
            proposals.append(
                {
                    "instrument": ins.id,
                    "ticker": tickers.get(ins.id, str(ins.id)),
                    "side": "SELL",
                    "quantity": -diff,
                    "reason": "Reducir al peso objetivo.",
                }
            )
    return {
        "experiment": experiment.id,
        "verdict": experiment.verdict,
        "top_k": top_k,
        "proposals": proposals,
        "warnings": warnings,
    }
