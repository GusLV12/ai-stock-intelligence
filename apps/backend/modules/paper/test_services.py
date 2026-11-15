from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from django.db import models

from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.models import TaxLot, Transaction
from modules.pricing.history_models import DailyClose
from modules.pricing.services import record_price
from modules.quant.models import QuantExperiment

from . import services as svc
from .engine import PaperError
from .models import PaperAccount, PaperJournalEntry, PaperOrder

NOW = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)


@pytest.fixture
def ctx(db):
    user = get_user_model().objects.create_user("ana", password="x")
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=2)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    record_price(ins, D(100), "MANUAL", NOW - timedelta(hours=2))
    acc = svc.create_account(user, "Práctica", D(10_000), {"slippage_bps": "10", "max_position_pct": "0.5"})
    return user, ins, acc


def buy(acc, ins, qty, **kw):
    return svc.place_order(acc, ins, "BUY", D(qty), now=kw.pop("now", NOW), **kw)


def test_compra_a_mercado_con_deslizamiento_y_procedencia(ctx):
    _, ins, acc = ctx
    o = buy(acc, ins, 10)
    assert (
        o.status == "FILLED" and o.reference_price == D(100) and o.fill_price == D("100.1000")
    )  # 10 pb en contra
    assert (
        o.adverse_slippage_bps == D("10.0000")
        and o.reference_source["provider"] == "MANUAL"
        and o.reference_source["age_days"] == pytest.approx(0.08, abs=0.01)
    )
    s = svc.summary(acc, NOW)
    assert s["cash"] == D(10_000) - D("1001.0")  # 10 * 100.1
    assert s["positions"][0]["quantity"] == D(10) and s["positions"][0]["avg_cost"] == D("100.1")
    assert s["equity"] == D(10_000) - D(1001) + D(1000) and "no hay dinero real" in s["notice"]


def test_venta_realiza_utilidad_fifo_y_baja_la_posicion(ctx):
    _, ins, acc = ctx
    buy(acc, ins, 10)
    record_price(ins, D(110), "MANUAL", NOW - timedelta(minutes=5))
    o = svc.place_order(acc, ins, "SELL", D(4), now=NOW)
    assert o.fill_price == D("109.8900")  # 110 menos 10 pb
    s = svc.summary(acc, NOW)
    assert s["realized"] == D(4) * (D("109.89") - D("100.1")) and s["positions"][0]["quantity"] == D(6)


def test_rechazos_de_negocio_se_guardan_con_su_motivo(ctx):
    _, ins, acc = ctx
    o = buy(acc, ins, 80)  # 8 000 de 10 000 = 80 % > 50 %
    assert o.status == "REJECTED" and "50%" in o.reason
    o = svc.place_order(acc, ins, "SELL", D(1), now=NOW)
    assert o.status == "REJECTED" and "corto" in o.reason
    o = buy(acc, ins, 200)  # más de lo que alcanza el efectivo
    assert o.status == "REJECTED" and "Efectivo" in o.reason
    assert PaperOrder.objects.filter(account=acc, status="REJECTED").count() == 3
    assert svc.summary(acc, NOW)["cash"] == D(10_000)  # nada se movió


def test_precio_viejo_o_ausente_rechaza(ctx):
    _, ins, acc = ctx
    viejo = buy(acc, ins, 1, now=NOW + timedelta(days=9))
    assert (
        viejo.status == "REJECTED"
        and "desactualizado" in viejo.reason
        and viejo.reference_source["age_days"] > 5
    )
    otro = Instrument.objects.create(asset_class="STOCK", name="BBB", quantity_scale=0)
    sin = svc.place_order(acc, otro, "BUY", D(1), now=NOW)
    assert sin.status == "REJECTED" and "ningún precio" in sin.reason


def test_moneda_distinta_a_la_de_la_cuenta_rechaza(ctx):
    _, _, acc = ctx
    mx = Instrument.objects.create(asset_class="STOCK", name="MX", quantity_scale=0, currency="MXN")
    record_price(mx, D(50), "MANUAL", NOW - timedelta(hours=1))
    o = svc.place_order(acc, mx, "BUY", D(1), now=NOW)
    assert o.status == "REJECTED" and "MXN" in o.reason


@pytest.mark.parametrize("qty", ["0", "-1", "1.234", "NaN", "abc"])
def test_errores_de_captura_no_dejan_rastro(ctx, qty):
    _, ins, acc = ctx
    with pytest.raises(PaperError):
        svc.place_order(acc, ins, "BUY", D(qty) if qty != "abc" else "abc", now=NOW)
    assert PaperOrder.objects.count() == 0


def test_validaciones_de_orden(ctx):
    _, ins, acc = ctx
    for kw in (
        {"side": "HOLD"},
        {"order_type": "STOP"},
        {"order_type": "LIMIT"},
        {"order_type": "LIMIT", "limit_price": D(-1)},
        {"order_type": "LIMIT", "limit_price": D("NaN")},
    ):
        with pytest.raises(PaperError):
            svc.place_order(acc, ins, **{"side": "BUY", "quantity": D(1), "now": NOW, **kw})
    inv = Instrument.objects.create(asset_class="STOCK", name="R", quantity_scale=0, research_only=True)
    with pytest.raises(PaperError, match="investigación"):
        svc.place_order(acc, inv, "BUY", D(1), now=NOW)
    with pytest.raises(PaperError):
        svc.create_account(acc.user, "x", D(5))


def test_orden_limitada_espera_y_se_ejecuta_sin_pagar_mas_que_el_limite(ctx):
    _, ins, acc = ctx
    o = svc.place_order(acc, ins, "BUY", D(10), order_type="LIMIT", limit_price=D(95), now=NOW)
    assert o.status == "PENDING" and o.fill_price is None
    assert svc.process_pending(acc, NOW)["still_pending"] == [o.id]  # 100 > 95
    record_price(ins, D("94.5"), "MANUAL", NOW + timedelta(hours=3))
    res = svc.process_pending(acc, NOW + timedelta(hours=4))
    o.refresh_from_db()
    assert (
        res["filled"] == [o.id] and o.status == "FILLED" and o.fill_price == D("94.5945")
    )  # 94.5 + 10 pb, bajo el límite de 95
    # un límite que ya es ejecutable al crearse se ejecuta de inmediato, sin superar el límite
    o2 = svc.place_order(
        acc, ins, "BUY", D(1), order_type="LIMIT", limit_price=D("94.55"), now=NOW + timedelta(hours=4)
    )
    assert o2.status == "FILLED" and o2.fill_price == D("94.5500")


def test_ordenes_limitadas_vencen_y_se_cancelan(ctx):
    _, ins, acc = ctx
    a = svc.place_order(acc, ins, "BUY", D(1), order_type="LIMIT", limit_price=D(50), now=NOW)
    b = svc.place_order(acc, ins, "SELL", D(1), order_type="LIMIT", limit_price=D(500), now=NOW)
    svc.cancel_order(b, NOW)
    assert b.status == "CANCELLED"
    PaperOrder.objects.filter(pk=a.pk).update(created_at=NOW - timedelta(days=40))
    assert svc.process_pending(acc, NOW)["expired"] == [a.id]
    a.refresh_from_db()
    assert a.status == "EXPIRED"
    with pytest.raises(PaperError):
        svc.cancel_order(a)


def test_una_orden_decidida_es_inmutable_y_nada_se_borra(ctx):
    _user, ins, acc = ctx
    o = buy(acc, ins, 1)
    o.fill_price = D(1)
    with pytest.raises(models.ProtectedError):
        o.save()
    with pytest.raises(models.ProtectedError):
        o.delete()
    with pytest.raises(models.ProtectedError):
        acc.delete()
    e = PaperJournalEntry.objects.create(account=acc, order=o, kind="DECISION", text="Entrada por prueba")
    e.text = "cambiado"
    with pytest.raises(models.ProtectedError):
        e.save()
    with pytest.raises(models.ProtectedError):
        e.delete()


def test_curva_de_capital_y_metricas(ctx):
    _, ins, acc = ctx
    PaperAccount.objects.filter(pk=acc.pk).update(
        created_at=NOW - timedelta(days=2)
    )  # la cuenta existe desde antes de operar
    acc.refresh_from_db()
    buy(acc, ins, 10, now=NOW)
    for i, px in enumerate([100, 102, 99, 105]):
        DailyClose.objects.create(
            instrument=ins,
            date=date(2026, 11, 10) + timedelta(days=i),
            close=D(px),
            currency="USD",
            provider="Y",
        )
    svc.place_order(acc, ins, "SELL", D(10), now=NOW + timedelta(days=3, hours=2))
    curve = svc.equity_curve(acc)
    assert curve[0]["equity"] == D(10_000) and [c["date"] for c in curve[1:]] == [
        date(2026, 11, 10) + timedelta(days=i) for i in range(4)
    ]
    # 10/11: 10 títulos a 100 (precio de cierre) + efectivo 8 999 = 9 999
    assert curve[1]["equity"] == D(8999) + D(1000)
    m = svc.metrics(acc, NOW)
    assert m["closed_trades"] == 1 and m["win_rate"] == 1.0 and m["orders"]["FILLED"] == 2
    assert m["max_drawdown"] < 0 and m["sharpe"] is None and "30 observaciones" in m["sharpe_note"]
    assert m["avg_adverse_slippage_bps"] == D(10) and m["sample_note"]
    assert m["total_return"] == pytest.approx(float(curve[-1]["equity"] / D(10_000) - 1))


def test_el_paper_trading_no_toca_el_libro_contable_real(ctx):
    _, ins, acc = ctx
    antes = (Transaction.objects.count(), TaxLot.objects.count())
    buy(acc, ins, 5)
    svc.place_order(acc, ins, "SELL", D(2), now=NOW)
    svc.process_pending(acc, NOW)
    assert (Transaction.objects.count(), TaxLot.objects.count()) == antes


def exp(user, verdict, ranking):
    return QuantExperiment.objects.create(
        user=user, name="e", horizon=20, config={}, dataset_hash="a" * 64, result_hash="b" * 64, quant_version="1", candidate="ridge", verdict=verdict,
        result={"latest_scores": [{"instrument": i, "score": s} for i, s in ranking]},
    )  # fmt: skip


def test_propuesta_desde_un_experimento_equipondera_y_advierte(ctx):
    user, ins, acc = ctx
    b = Instrument.objects.create(asset_class="STOCK", name="BBB", quantity_scale=0)
    InstrumentIdentifier.objects.create(instrument=b, kind="TICKER", value="BBB")
    record_price(b, D(50), "MANUAL", NOW - timedelta(hours=1))
    e = exp(user, "NO_PROMOVIDO", [(ins.id, 0.5), (b.id, 0.2)])
    p = svc.propose_from_experiment(acc, e, top_k=2, now=NOW)
    qty = {x["ticker"]: x["quantity"] for x in p["proposals"]}
    # valor objetivo por emisora = 10 000 * 0.98 / 2 = 4 900
    assert qty["AAA"] == (D(4900) / D("100.1")).quantize(D("0.01"), rounding="ROUND_DOWN") and qty[
        "BBB"
    ] == D(97)
    assert all(x["side"] == "BUY" for x in p["proposals"]) and any(
        "NO fue promovido" in w for w in p["warnings"]
    )
    assert PaperOrder.objects.count() == 0  # propone, no crea
    # tras comprar AAA, y salir del top, se propone vender
    buy(acc, ins, 10)
    p2 = svc.propose_from_experiment(
        acc, exp(user, "PROMOVER_A_SEGUIMIENTO", [(b.id, 0.9)]), top_k=1, now=NOW
    )
    assert {(x["ticker"], x["side"]) for x in p2["proposals"]} == {("AAA", "SELL"), ("BBB", "BUY")}
    assert not any("NO fue promovido" in w for w in p2["warnings"])


def test_propuesta_omite_instrumentos_de_investigacion_o_sin_precio(ctx):
    user, _ins, acc = ctx
    r = Instrument.objects.create(asset_class="STOCK", name="R", quantity_scale=0, research_only=True)
    sin = Instrument.objects.create(asset_class="STOCK", name="S", quantity_scale=0)
    p = svc.propose_from_experiment(
        acc, exp(user, "NO_PROMOVIDO", [(r.id, 1), (sin.id, 0.5)]), top_k=2, now=NOW
    )
    assert p["proposals"] == [] and len(p["warnings"]) >= 3


def test_la_curva_tiene_un_solo_punto_por_fecha_aunque_se_opere_el_dia_de_la_creacion(ctx):
    _, ins, acc = ctx
    hoy = datetime.now(UTC)
    PaperAccount.objects.filter(pk=acc.pk).update(created_at=hoy)
    acc.refresh_from_db()
    record_price(ins, D(100), "MANUAL", hoy - timedelta(minutes=5))
    svc.place_order(acc, ins, "BUY", D(1), now=hoy)
    fechas = [c["date"] for c in svc.equity_curve(acc)]
    assert fechas == sorted(set(fechas)) and len(fechas) == len(set(fechas))


def test_propuesta_no_opera_por_diferencias_insignificantes(ctx):
    user, ins, acc = ctx
    e = exp(user, "PROMOVER_A_SEGUIMIENTO", [(ins.id, 1.0)])
    p = svc.propose_from_experiment(acc, e, top_k=1, now=NOW)
    assert [x["side"] for x in p["proposals"]] == ["BUY"]
    buy(acc, ins, p["proposals"][0]["quantity"])
    record_price(ins, D("100.2"), "MANUAL", NOW + timedelta(minutes=1))  # el precio se mueve un poco
    assert svc.propose_from_experiment(acc, e, top_k=1, now=NOW + timedelta(minutes=2))["proposals"] == []
