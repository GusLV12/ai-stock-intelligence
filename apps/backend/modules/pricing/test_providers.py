from datetime import UTC, datetime
from decimal import Decimal as D
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy

from .models import FxRate, MarketPrice
from .providers import BanxicoFixProvider, ProviderError, TwelveDataProvider, http_get_json
from .refresh import held_tickers, refresh_fx, refresh_prices

SECRET = "clave-secreta-de-prueba"
QUOTE = {
    "symbol": "AAA", "currency": "USD", "datetime": "2026-11-13", "timestamp": 1794580200,
    "last_quote_at": 1794603540, "close": "356.26999", "is_market_open": False,
}  # fmt: skip
BANXICO = {"bmx": {"series": [{"idSerie": "SF43718", "titulo": "FIX", "datos": [{"fecha": "13/11/2026", "dato": "18.4930"}]}]}}


def fake_http(responses):
    calls = []

    def http(url, headers=None, timeout=15):
        calls.append((url, headers))
        r = responses[len(calls) - 1] if isinstance(responses, list) else responses
        if isinstance(r, Exception):
            raise r
        return r

    http.calls = calls
    return http


def td(responses, **kw):
    return TwelveDataProvider(SECRET, http=fake_http(responses), sleep=lambda s: None, **kw)


def test_quote_usa_la_hora_real_de_la_cotizacion():
    q = td(QUOTE).fetch_quote("AAA")
    assert q.price == D("356.26999") and q.currency == "USD" and not q.market_open
    assert q.observed_at == datetime.fromtimestamp(1794603540, UTC)  # last_quote_at, no "ahora"


def test_quote_sin_last_quote_usa_timestamp():
    data = {k: v for k, v in QUOTE.items() if k != "last_quote_at"}
    assert td(data).fetch_quote("AAA").observed_at == datetime.fromtimestamp(1794580200, UTC)


@pytest.mark.parametrize(
    "data,msg",
    [
        ({"code": 400, "message": "symbol invalid", "status": "error"}, "rechazó"),
        ({"code": 429, "message": "limit", "status": "error"}, "Límite"),
        ({**QUOTE, "close": "abc"}, "Precio inválido"),
        ({**QUOTE, "close": "-1"}, "Precio inválido"),
        ({**QUOTE, "close": None}, "Precio inválido"),
        ({**QUOTE, "last_quote_at": "x", "timestamp": None}, "Hora de cotización"),
        ({**QUOTE, "currency": ""}, "moneda"),
    ],
)
def test_respuestas_invalidas_dan_error_controlado(data, msg):
    with pytest.raises(ProviderError, match=msg):
        td(data).fetch_quote("AAA")


def test_limita_la_frecuencia_segun_el_plan():
    sleeps, t = [], [0.0]
    p = TwelveDataProvider(SECRET, per_minute=8, http=fake_http(QUOTE), sleep=sleeps.append, clock=lambda: t[0])
    p.fetch_quote("A")
    p.fetch_quote("B")  # sin avanzar el reloj: debe esperar 60/8 s
    assert sleeps == [7.5]


def test_la_clave_va_en_la_url_pero_nunca_en_los_errores():
    http = fake_http(QUOTE)
    TwelveDataProvider(SECRET, http=http, sleep=lambda s: None).fetch_quote("AAA")
    assert http.calls[0][0].startswith("https://api.twelvedata.com/quote?") and "symbol=AAA" in http.calls[0][0]
    with pytest.raises(ProviderError) as exc:
        td({"code": 401, "message": f"bad {SECRET}", "status": "error"}).fetch_quote("AAA")
    assert SECRET not in str(exc.value)


def test_http_get_json_exige_https_y_no_filtra_la_url():
    with pytest.raises(ProviderError, match="HTTPS"):
        http_get_json("http://api.twelvedata.com/quote?apikey=" + SECRET)
    with pytest.raises(ProviderError) as exc:
        http_get_json("https://127.0.0.1:9/quote?apikey=" + SECRET, timeout=2)
    assert SECRET not in str(exc.value)


def test_proveedor_sin_clave():
    with pytest.raises(ProviderError, match="TWELVE_DATA_API_KEY"):
        TwelveDataProvider("")
    with pytest.raises(ProviderError, match="BANXICO_TOKEN"):
        BanxicoFixProvider("")


def test_banxico_fix_y_hora_de_determinacion():
    http = fake_http(BANXICO)
    rate, at = BanxicoFixProvider("tok", http=http).fetch_fix()
    assert rate == D("18.4930")
    assert at.hour == 12 and str(at.tzinfo) == "America/Mexico_City" and (at.day, at.month) == (13, 11)
    assert http.calls[0][1]["Bmx-Token"] == "tok" and "SF43718" in http.calls[0][0]


@pytest.mark.parametrize(
    "data,msg",
    [
        ({"bmx": {"series": [{"datos": [{"fecha": "13/11/2026", "dato": "N/E"}]}]}}, "aún no publica"),
        ({"bmx": {"series": []}}, "formato inesperado"),
        ({}, "formato inesperado"),
        ({"bmx": {"series": [{"datos": [{"fecha": "2026-11-13", "dato": "18.5"}]}]}}, "Fecha inválida"),
        ({"bmx": {"series": [{"datos": [{"fecha": "13/11/2026", "dato": "0"}]}]}}, "inválido"),
    ],
)
def test_banxico_respuestas_invalidas(data, msg):
    with pytest.raises(ProviderError, match=msg):
        BanxicoFixProvider("tok", http=fake_http(data)).fetch_fix()


@pytest.fixture
def held(db):
    user = get_user_model().objects.create_user("ana", password="x")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    out = {}
    for t in ("AAA", "BBB", "CCC"):
        ins = Instrument.objects.create(asset_class="STOCK", name=t, quantity_scale=8)
        InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=t)
        out[t] = ins
    for t in ("AAA", "BBB"):  # CCC no tiene posición: no se consulta
        record_buy(account=acc, instrument=out[t], quantity=D(1), price=D(10), executed_at=datetime(2026, 1, 1, tzinfo=UTC))
    return out


def test_solo_se_consultan_emisoras_con_posicion(held):
    assert set(held_tickers()) == {"AAA", "BBB"}


def test_refresh_guarda_con_proveedor_y_es_idempotente(held):
    http = fake_http(QUOTE)
    res = refresh_prices(TwelveDataProvider(SECRET, http=http, sleep=lambda s: None))
    assert res["updated"] == ["AAA", "BBB"] and not res["failed"] and len(http.calls) == 2
    p = MarketPrice.objects.filter(instrument=held["AAA"]).get()
    assert p.provider == "TWELVE_DATA" and p.observed_at == datetime.fromtimestamp(1794603540, UTC)
    again = refresh_prices(TwelveDataProvider(SECRET, http=fake_http(QUOTE), sleep=lambda s: None))
    assert again["unchanged"] == ["AAA", "BBB"] and MarketPrice.objects.count() == 2


def test_un_fallo_no_detiene_el_resto_y_la_moneda_se_valida(held):
    bad_currency = {**QUOTE, "currency": "MXN"}
    http = fake_http([{"code": 400, "message": "x", "status": "error"}, bad_currency])
    res = refresh_prices(TwelveDataProvider(SECRET, http=http, sleep=lambda s: None))
    assert set(res["failed"]) == {"AAA", "BBB"} and "moneda" in res["failed"]["BBB"]
    assert MarketPrice.objects.count() == 0


def test_limite_del_proveedor_aborta_para_no_gastar_cuota(held):
    http = fake_http({"code": 429, "message": "limit", "status": "error"})
    res = refresh_prices(TwelveDataProvider(SECRET, http=http, sleep=lambda s: None))
    assert len(http.calls) == 1 and "_abortado" in res["failed"]


def test_refresh_fx(db):
    res = refresh_fx(BanxicoFixProvider("tok", http=fake_http(BANXICO)))
    assert res["ok"] and res["created"] and FxRate.objects.get().provider == "BANXICO_FIX"
    assert refresh_fx(BanxicoFixProvider("tok", http=fake_http(BANXICO)))["created"] is False
    bad = refresh_fx(BanxicoFixProvider("tok", http=fake_http({})))
    assert bad["ok"] is False and "formato" in bad["error"]


def test_comando_sin_claves_se_omite_sin_fallar(db, monkeypatch):
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    monkeypatch.delenv("BANXICO_TOKEN", raising=False)
    out = StringIO()
    call_command("refresh_market_data", stdout=out)
    assert "omitido" in out.getvalue() and out.getvalue().count("omitido") == 2
