from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy

from .history import refresh_history
from .history_models import DailyClose
from .providers import YahooProvider
from .test_fallback import fake_http

NOW = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)
BASE = int(datetime(2026, 11, 10, 14, 30, tzinfo=UTC).timestamp())


def response(opens, highs, lows, closes, volumes, n=None):
    n = n or len(closes)
    stamps = [BASE + i * 86400 for i in range(n)]
    quote = {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}
    return {"chart": {"result": [{"meta": {"currency": "USD", "gmtoffset": -18000}, "timestamp": stamps,
                                  "indicators": {"quote": [quote]}}], "error": None}}  # fmt: skip


def provider(resp):
    return YahooProvider(http=fake_http(resp), sleep=lambda s: None, now=lambda: NOW)


def test_velas_completas_con_volumen():
    resp = response([10.0, 11.0], [11.5, 12.0], [9.5, 10.5], [11.0, 11.8], [1000, 2500])
    out = provider(resp).fetch_history("AAA", "1mo")
    bars = {b["date"]: b for b in out["ohlcv"]}
    assert bars[date(2026, 11, 10)] == {"date": date(2026, 11, 10), "close": D("11.0000"), "open": D("10.0000"),
                                        "high": D("11.5000"), "low": D("9.5000"), "volume": 1000}  # fmt: skip
    assert [c for _, c in out["bars"]] == [D("11.0000"), D("11.8000")]  # la forma anterior se conserva


@pytest.mark.parametrize(
    "o,h,low,c",
    [(10.0, 9.0, 8.0, 9.5),  # high por debajo del cierre
     (10.0, 12.0, 11.0, 11.5),  # low por encima de la apertura
     (None, 12.0, 9.0, 10.0), (10.0, None, 9.0, 10.0), (10.0, 12.0, None, 10.0), (-1.0, 12.0, 9.0, 10.0)],
)
def test_vela_incoherente_o_incompleta_solo_conserva_el_cierre(o, h, low, c):
    out = provider(response([o], [h], [low], [c], [100])).fetch_history("AAA", "1mo")
    bar = out["ohlcv"][0]
    assert bar["close"] == D(str(c)).quantize(D("0.0001"))
    assert bar["open"] is None and bar["high"] is None and bar["low"] is None and bar["volume"] is None


def test_volumen_invalido_no_invalida_la_vela():
    for vol in (None, -5, "x"):
        bar = provider(response([10.0], [11.0], [9.0], [10.5], [vol])).fetch_history("AAA", "1mo")["ohlcv"][0]
        assert bar["high"] == D("11.0000") and bar["volume"] is None


@pytest.fixture
def held(db):
    user = get_user_model().objects.create_user("ana", password="x")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=8)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    record_buy(account=acc, instrument=ins, quantity=D(1), price=D(10), executed_at=datetime(2026, 1, 1, tzinfo=UTC))
    return ins


def test_se_guardan_las_velas_y_no_se_duplican(held):
    resp = response([10.0, 11.0], [11.5, 12.0], [9.5, 10.5], [11.0, 11.8], [1000, 2500])
    res = refresh_history(provider(resp), "1mo")
    assert res["saved"] == {"AAA": 2}
    row = DailyClose.objects.get(date=date(2026, 11, 10))
    assert (row.open, row.high, row.low, row.close, row.volume) == (D(10), D("11.5"), D("9.5"), D(11), 1000)
    again = refresh_history(provider(resp), "1mo")
    assert again["saved"] == {} and DailyClose.objects.count() == 2


def test_cierres_guardados_sin_vela_se_completan_sin_tocar_el_cierre(held):
    DailyClose.objects.create(instrument=held, date=date(2026, 11, 10), close=D(11), currency="USD", provider="YAHOO_UNOFFICIAL")
    resp = response([10.0], [11.5], [9.5], [11.0], [1000])
    res = refresh_history(provider(resp), "1mo")
    row = DailyClose.objects.get()
    assert res["saved"] == {"AAA": 1} and (row.high, row.volume, row.close) == (D("11.5"), 1000, D(11))
    # el cierre ya guardado manda aunque el proveedor lo cambie después
    changed = response([10.0], [11.5], [9.5], [99.0], [1000])
    changed["chart"]["result"][0]["indicators"]["quote"][0]["high"] = [120.0]
    refresh_history(provider(changed), "1mo")
    assert DailyClose.objects.get().close == D(11)
