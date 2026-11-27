import math
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.imports.models import PositionSnapshot
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy
from modules.pricing.history_models import DailyClose
from modules.pricing.services import record_price

from . import risk

START = date(2025, 1, 1)


def bars(returns_, start_price=100.0, start=START):
    p, out = start_price, [(start, start_price)]
    for i, r in enumerate(returns_, start=1):
        p *= 1 + r
        out.append((start + timedelta(days=i), p))
    return out


def test_retornos_y_caida_maxima_ejemplo_manual():
    b = [(START + timedelta(days=i), p) for i, p in enumerate([100.0, 120.0, 90.0, 110.0])]
    assert [round(r, 6) for _, r in risk.returns(b)] == [0.2, -0.25, round(110 / 90 - 1, 6)]
    assert risk.max_drawdown(b) == pytest.approx(-0.25)  # de 120 a 90
    assert risk.max_drawdown([(START, 1.0), (START + timedelta(days=1), 2.0)]) == 0.0


def test_volatilidad_anualizada_de_una_serie_alterna_conocida():
    r = [0.01, -0.01] * 40  # media ~0, desviación muestral conocida
    res = risk.analyze({"A": bars(r)})
    expected = math.sqrt(sum((x - 0) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(252)
    assert res["assets"]["A"]["annualized_volatility"] == pytest.approx(expected, rel=1e-3)
    assert res["assets"]["A"]["observations"] == 80


def test_correlacion_perfecta_inversa_y_sin_variacion():
    rng = random.Random(1)
    base = [rng.uniform(-0.02, 0.02) for _ in range(100)]
    series = {"A": bars(base), "B": bars(base), "C": bars([-x for x in base])}
    m = risk.analyze(series)["correlation_matrix"]
    assert m["A"]["B"] == pytest.approx(1.0, abs=1e-6) and m["A"]["C"] == pytest.approx(-1.0, abs=1e-3)
    assert m["A"]["A"] == 1.0
    plano = risk.analyze({"A": bars(base), "P": bars([0.0] * 100)})["correlation_matrix"]
    assert plano["A"]["P"] is None  # serie constante: correlación indefinida, no 0


def test_historial_insuficiente_se_omite_con_motivo():
    res = risk.analyze({"A": bars([0.01] * 5), "B": bars([0.01, -0.01] * 40)})
    assert "A" in res["skipped"] and "insuficiente" in res["skipped"]["A"] and "B" in res["assets"]
    assert any("sin historial suficiente" in w.lower() for w in res["warnings"])


def test_poca_superposicion_no_correlaciona():
    a = bars([0.01, -0.01] * 40, start=START)
    b = bars([0.02, -0.02] * 40, start=START + timedelta(days=70))  # fechas casi disjuntas
    assert risk.analyze({"A": a, "B": b})["correlation_matrix"]["A"]["B"] is None


def test_la_diversificacion_reduce_la_volatilidad_del_portafolio():
    rng = random.Random(7)
    a = [rng.uniform(-0.02, 0.02) for _ in range(150)]
    b = [rng.uniform(-0.02, 0.02) for _ in range(150)]  # independiente de A
    res = risk.analyze({"A": bars(a), "B": bars(b)}, {"A": 0.5, "B": 0.5})
    p = res["portfolio"]
    assert (
        p["annualized_volatility"] < p["weighted_average_asset_volatility"]
        and p["diversification_benefit"] > 0
    )
    assert p["covered_share_of_invested"] == pytest.approx(1.0)


def test_el_efectivo_aporta_volatilidad_cero_y_la_cobertura_parcial_se_avisa():
    r = [0.01, -0.01] * 40
    con = risk.analyze({"A": bars(r)}, {"A": 0.5, "CASH": 0.5})["portfolio"]
    solo = risk.analyze({"A": bars(r)}, {"A": 1.0})["portfolio"]
    assert con["annualized_volatility"] == pytest.approx(solo["annualized_volatility"] * 0.5, rel=1e-6)
    parcial = risk.analyze({"A": bars(r)}, {"A": 0.3, "SIN": 0.7})
    assert any("cubren 30%" in w for w in parcial["warnings"])


def test_siempre_avisa_que_es_historico():
    assert "no son una predicción" in risk.analyze({"A": bars([0.01, -0.01] * 40)})["warnings"][0]


# ---------- historial de Yahoo y refresco ----------

NOW = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)


def yahoo_history(days=70, price=100.0, hoy=True):
    stamps, closes = [], []
    base = int(datetime(2026, 11, 14, 14, 30, tzinfo=UTC).timestamp())
    for i in range(days):
        stamps.append(base - (days - 1 - i) * 86400)
        closes.append(price + i * 0.5)
    if hoy:
        pass
    return {"chart": {"result": [{"meta": {"currency": "USD", "gmtoffset": -18000},
                                  "timestamp": stamps, "indicators": {"quote": [{"close": closes}]}}], "error": None}}  # fmt: skip


def provider(response):
    from modules.pricing.providers import YahooProvider
    from modules.pricing.test_fallback import fake_http

    return YahooProvider(http=fake_http(response), sleep=lambda s: None, now=lambda: NOW)


def test_fetch_history_descarta_hoy_nulos_y_no_positivos():
    resp = yahoo_history(5)
    q = resp["chart"]["result"][0]["indicators"]["quote"][0]["close"]
    q[1], q[2] = None, -3.0
    out = provider(resp).fetch_history("AAA", "3mo")
    days = [d for d, _ in out["bars"]]
    assert (
        out["currency"] == "USD" and date(2026, 11, 14) not in days
    )  # la barra de hoy (puede estar a medias)
    assert len(out["bars"]) == 2 and days == sorted(days)  # 5 barras: hoy, 1 nula y 1 negativa fuera


@pytest.mark.parametrize(
    "data,msg",
    [({"chart": {"result": None, "error": {"code": "x"}}}, "no tiene historial"), ({}, "formato"),
     ({"chart": {"result": [{"meta": {"currency": "USD"}}]}}, "formato"),
     ({"chart": {"result": [{"meta": {"currency": ""}, "timestamp": [1], "indicators": {"quote": [{"close": [1.0]}]}}]}}, "moneda"),
     ({"chart": {"result": [{"meta": {"currency": "USD"}, "timestamp": [1], "indicators": {"quote": [{"close": [None]}]}}]}}, "cierres válidos")],
)  # fmt: skip
def test_fetch_history_respuestas_invalidas(data, msg):
    from modules.pricing.providers import ProviderError

    with pytest.raises(ProviderError, match=msg):
        provider(data).fetch_history("AAA", "1y")


def test_rango_no_soportado():
    from modules.pricing.providers import ProviderError

    with pytest.raises(ProviderError, match="no soportado"):
        provider(yahoo_history()).fetch_history("AAA", "10y")


@pytest.fixture
def held(db):
    user = get_user_model().objects.create_user("ana", password="x")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    out = {}
    for t, cls in (("AAA", "STOCK"), ("BBB", "STOCK")):
        ins = Instrument.objects.create(asset_class=cls, name=t, quantity_scale=8)
        InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=t)
        record_buy(
            account=acc,
            instrument=ins,
            quantity=D(1),
            price=D(100),
            executed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        record_price(ins, D(100), "MANUAL", datetime.now(UTC))
        out[t] = ins
    PositionSnapshot.objects.create(
        account=acc, file_sha256="a" * 64, as_of=datetime.now(UTC), cash_amount=D(100)
    )
    return user, out


def test_refresh_history_es_idempotente_y_no_duplica(held):
    from modules.pricing.history import refresh_history

    p = provider(yahoo_history(40))
    res = refresh_history(p, "3mo")
    assert sum(res["saved"].values()) > 0 and not res["failed"]
    n = DailyClose.objects.count()
    again = refresh_history(provider(yahoo_history(40)), "3mo")
    assert (
        DailyClose.objects.count() == n
        and again["saved"] == {}
        and sorted(again["unchanged"]) == ["AAA", "BBB"]
    )
    assert {c.provider for c in DailyClose.objects.all()} == {"YAHOO_UNOFFICIAL"}


def test_refresh_history_aborta_si_el_proveedor_cae_y_valida_moneda(held):
    from modules.pricing.history import refresh_history
    from modules.pricing.providers import ProviderError

    caido = provider(ProviderError("caído", transient=True))
    res = refresh_history(caido, "1y")
    assert "_abortado" in res["failed"] and DailyClose.objects.count() == 0
    mxn = yahoo_history(40)
    mxn["chart"]["result"][0]["meta"]["currency"] = "MXN"
    res = refresh_history(provider(mxn), "1y")
    assert "moneda" in " ".join(res["failed"].values()) and DailyClose.objects.count() == 0


# ---------- API ----------


def client_for(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_riesgo_agrega_pesos_de_cuentas_repetidas(held, monkeypatch):
    from . import risk_api

    user, instruments = held
    other = InvestmentAccount.objects.create(owner=user, name="Segunda", broker="GBM_USA")
    record_buy(account=other, instrument=instruments["AAA"], quantity=D(2), price=D(100), executed_at=datetime(2026, 1, 1, tzinfo=UTC))
    captured = {}
    monkeypatch.setattr(risk_api, "_series_for", lambda *args: {})
    def analyze(series, weights):
        captured.update(weights)
        return {"assets": {}, "warnings": []}
    monkeypatch.setattr(risk_api.risk, "analyze", analyze)
    risk_api.risk_report(user, 365)
    assert captured["AAA"] == pytest.approx(0.6)


def load_closes(ins, returns_):
    today = datetime.now(UTC).date()
    p, day = 100.0, today - timedelta(days=len(returns_) + 5)
    rows = [
        DailyClose(
            instrument=ins, date=day, close=D(str(round(p, 6))), currency="USD", provider="YAHOO_UNOFFICIAL"
        )
    ]
    for r in returns_:
        day += timedelta(days=1)
        p *= 1 + r
        rows.append(
            DailyClose(
                instrument=ins,
                date=day,
                close=D(str(round(p, 6))),
                currency="USD",
                provider="YAHOO_UNOFFICIAL",
            )
        )
    DailyClose.objects.bulk_create(rows)


def test_api_riesgo_con_ventana_y_cobertura(held):
    user, ins = held
    rng = random.Random(3)
    load_closes(ins["AAA"], [rng.uniform(-0.02, 0.02) for _ in range(120)])
    load_closes(ins["BBB"], [rng.uniform(-0.02, 0.02) for _ in range(120)])
    d = client_for(user).get("/api/v1/portfolio/risk?window_days=365").json()
    assert d["evaluable"] and set(d["assets"]) == {"AAA", "BBB"} and d["requested_window_days"] == 365
    assert d["portfolio"]["annualized_volatility"] is not None and "no predicen" in d["disclaimer"]
    assert d["correlation_matrix"]["AAA"]["BBB"] is not None and d["providers"] == ["YAHOO_UNOFFICIAL"]
    assert float(d["portfolio"]["covered_share_of_invested"]) == pytest.approx(1.0)


def test_api_avisa_que_emisoras_no_tienen_historial(held):
    user, ins = held
    load_closes(ins["AAA"], [0.01, -0.01] * 40)
    d = client_for(user).get("/api/v1/portfolio/risk").json()
    assert (
        any("refresh_price_history" in w and "BBB" in w for w in d["warnings"]) and "BBB" not in d["assets"]
    )


@pytest.mark.parametrize("w", ["10", "abc", "99999", "-5"])
def test_api_valida_la_ventana(held, w):
    assert client_for(held[0]).get(f"/api/v1/portfolio/risk?window_days={w}").status_code == 400


def test_api_sin_posiciones_y_autenticacion(held):
    otro = get_user_model().objects.create_user("beto", password="x")
    assert client_for(otro).get("/api/v1/portfolio/risk").json()["evaluable"] is False
    assert APIClient().get("/api/v1/portfolio/risk").status_code in (401, 403)


def test_los_cierres_se_redondean_a_4_decimales():
    resp = yahoo_history(3)
    resp["chart"]["result"][0]["indicators"]["quote"][0]["close"] = [356.2699890136719, 356.27, 356.2700001]
    out = provider(resp).fetch_history("AAA", "1mo")
    assert [c for _, c in out["bars"]] == [
        D("356.2700")
    ] * 2  # la barra de hoy se descarta; el ruido de float desaparece
