"""Cadena de proveedores con respaldo: Yahoo (no oficial) cuando Twelve Data o Banxico fallan."""

import urllib.error
import urllib.request
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
from .providers import BanxicoFixProvider, ProviderError, TwelveDataProvider, YahooProvider, http_get_json
from .refresh import refresh_fx, refresh_prices

SECRET = "clave-de-prueba"
NOW = datetime.fromtimestamp(1794610000, UTC)
YAHOO = {
    "chart": {
        "result": [
            {"meta": {"currency": "USD", "symbol": "AAA", "regularMarketPrice": 356.27, "regularMarketTime": 1794603601}}
        ],
        "error": None,
    }
}
YAHOO_FX = {
    "chart": {
        "result": [
            {"meta": {"currency": "MXN", "symbol": "MXN=X", "regularMarketPrice": 18.523, "regularMarketTime": 1794609216}}
        ],
        "error": None,
    }
}
BANXICO = {"bmx": {"series": [{"datos": [{"fecha": "13/11/2026", "dato": "18.4930"}]}]}}


def fake_http(responses):
    """Doble de http_get_json: una respuesta fija, o una lista consumida en orden. Una excepción se lanza."""
    calls = []

    def http(url, headers=None, timeout=15):
        calls.append((url, headers))
        r = responses[len(calls) - 1] if isinstance(responses, list) else responses
        if isinstance(r, Exception):
            raise r
        return r

    http.calls = calls
    return http


def yahoo(responses):
    return YahooProvider(http=fake_http(responses), sleep=lambda s: None, now=lambda: NOW)


def twelve(responses):
    return TwelveDataProvider(SECRET, http=fake_http(responses), sleep=lambda s: None)


@pytest.fixture
def held(db):
    user = get_user_model().objects.create_user("ana", password="x")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    out = {}
    for t in ("AAA", "BBB", "CCC"):
        ins = Instrument.objects.create(asset_class="STOCK", name=t, quantity_scale=8)
        InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=t)
        record_buy(account=acc, instrument=ins, quantity=D(1), price=D(10), executed_at=datetime(2026, 1, 1, tzinfo=UTC))
        out[t] = ins
    return out


# ---------- adaptador de Yahoo ----------


def test_yahoo_quote_y_hora_real():
    q = yahoo(YAHOO).fetch_quote("AAA")
    assert q.price == D("356.27") and q.currency == "USD"
    assert q.observed_at == datetime.fromtimestamp(1794603601, UTC)


def test_yahoo_manda_solo_el_simbolo_y_un_agente_generico():
    http = fake_http(YAHOO)
    YahooProvider(http=http, sleep=lambda s: None, now=lambda: NOW).fetch_quote("BRK.B")
    url, headers = http.calls[0]
    assert url.startswith("https://query2.finance.yahoo.com/v8/finance/chart/BRK-B?")
    assert headers["User-Agent"] == "ai-stock-intelligence/1.0" and "@" not in str(headers)


def test_yahoo_cae_al_segundo_servidor_solo_si_el_error_es_transitorio():
    http = fake_http([ProviderError("HTTP 500", transient=True), YAHOO])
    assert YahooProvider(http=http, sleep=lambda s: None, now=lambda: NOW).fetch_quote("AAA").price == D("356.27")
    assert "query1" in http.calls[1][0]
    perm = fake_http([ProviderError("HTTP 404", transient=False), YAHOO])
    with pytest.raises(ProviderError, match="no tiene datos") as exc:
        YahooProvider(http=perm, sleep=lambda s: None, now=lambda: NOW).fetch_quote("ZZZ")
    assert exc.value.transient is False and len(perm.calls) == 1  # un símbolo inexistente no se reintenta


def _meta(**over):
    meta = {"regularMarketPrice": 5, "regularMarketTime": 1794603601, "currency": "USD"}
    meta.update(over)
    return {"chart": {"result": [{"meta": meta}]}}


@pytest.mark.parametrize(
    "data,msg,transient",
    [
        ({"chart": {"result": None, "error": {"code": "Not Found"}}}, "no tiene datos", False),
        ({"chart": "x"}, "formato inesperado", True),
        ({}, "formato inesperado", True),
        ({"chart": {"result": []}}, "formato inesperado", True),
        (_meta(regularMarketPrice="abc"), "Precio", False),
        (_meta(regularMarketPrice=0), "inválido", False),
        (_meta(regularMarketTime="x"), "Hora", False),
        (_meta(regularMarketTime=9999999999), "futuro", False),
        (_meta(currency=None), "moneda", False),
    ],
)
def test_yahoo_respuestas_invalidas(data, msg, transient):
    with pytest.raises(ProviderError, match=msg) as exc:
        yahoo(data).fetch_quote("AAA")
    assert exc.value.transient is transient


def test_yahoo_fx_usd_mxn():
    rate, at = yahoo(YAHOO_FX).fetch_fx()
    assert rate == D("18.523") and at == datetime.fromtimestamp(1794609216, UTC)
    with pytest.raises(ProviderError, match="par distinto"):
        yahoo(YAHOO).fetch_fx()  # devolvió USD, no MXN


@pytest.mark.parametrize("code,transient", [(429, True), (503, True), (404, False), (401, False)])
def test_errores_http_se_clasifican_como_transitorios_o_permanentes(monkeypatch, code, transient):
    def raiser(*args, **kwargs):
        raise urllib.error.HTTPError("https://x", code, "m", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", raiser)
    with pytest.raises(ProviderError) as exc:
        http_get_json("https://api.example.com/x")
    assert exc.value.transient is transient


def test_red_caida_es_transitoria():
    with pytest.raises(ProviderError) as exc:  # la red está bloqueada en pruebas (conftest)
        http_get_json("https://api.example.com/x")
    assert exc.value.transient is True


# ---------- cadena de respaldo ----------


def test_precios_usan_el_respaldo_cuando_el_principal_esta_caido(held):
    principal = twelve(ProviderError("No se pudo conectar", transient=True))
    res = refresh_prices([principal, yahoo(YAHOO)])
    assert res["updated"] == ["AAA", "BBB", "CCC"] and res["fallback_used"] == ["AAA", "BBB", "CCC"]
    assert not res["failed"]
    assert {p.provider for p in MarketPrice.objects.all()} == {"YAHOO_UNOFFICIAL"}  # se guarda quién respondió


def test_si_el_principal_responde_no_se_toca_el_respaldo(held):
    quote = {"symbol": "AAA", "currency": "USD", "timestamp": 1794580200, "last_quote_at": 1794603540, "close": "10"}
    respaldo_http = fake_http(YAHOO)
    respaldo = YahooProvider(http=respaldo_http, sleep=lambda s: None, now=lambda: NOW)
    res = refresh_prices([twelve(quote), respaldo])
    assert res["fallback_used"] == [] and respaldo_http.calls == []
    assert {p.provider for p in MarketPrice.objects.all()} == {"TWELVE_DATA"}


def test_cortacircuito_deja_de_insistir_en_el_proveedor_caido(held):
    caido = fake_http(ProviderError("No se pudo conectar", transient=True))
    principal = TwelveDataProvider(SECRET, http=caido, sleep=lambda s: None)
    res = refresh_prices([principal, yahoo(YAHOO)])
    assert len(caido.calls) == 2  # 3 emisoras, pero tras 2 fallos seguidos se salta el principal
    assert res["fallback_used"] == ["AAA", "BBB", "CCC"]


def test_limite_del_principal_abre_el_cortacircuito_al_instante(held):
    limite = fake_http({"code": 429, "message": "limit", "status": "error"})
    res = refresh_prices([TwelveDataProvider(SECRET, http=limite, sleep=lambda s: None), yahoo(YAHOO)])
    assert len(limite.calls) == 1 and res["fallback_used"] == ["AAA", "BBB", "CCC"]


def test_simbolo_inexistente_prueba_el_respaldo_sin_abrir_el_cortacircuito(held):
    invalido = fake_http({"code": 400, "message": "bad", "status": "error"})
    res = refresh_prices([TwelveDataProvider(SECRET, http=invalido, sleep=lambda s: None), yahoo(YAHOO)])
    assert res["fallback_used"] == ["AAA", "BBB", "CCC"] and len(invalido.calls) == 3  # siguió consultando


def test_si_todos_los_proveedores_caen_se_informa_sin_inventar_precios(held):
    p1 = twelve(ProviderError("caído", transient=True))
    p2 = yahoo(ProviderError("caído", transient=True))
    res = refresh_prices([p1, p2])
    assert MarketPrice.objects.count() == 0 and "_abortado" in res["failed"]
    assert any("TWELVE_DATA" in v and "YAHOO_UNOFFICIAL" in v for k, v in res["failed"].items() if k != "_abortado")


def test_moneda_distinta_en_el_respaldo_no_se_guarda(held):
    mxn = _meta(currency="MXN")
    res = refresh_prices([twelve(ProviderError("x", transient=True)), yahoo(mxn)])
    assert MarketPrice.objects.count() == 0 and "moneda" in " ".join(res["failed"].values())


def test_fx_usa_el_respaldo_si_banxico_falla_y_lo_indica(db):
    banxico = BanxicoFixProvider("tok", http=fake_http(ProviderError("HTTP 500", transient=True)))
    res = refresh_fx([banxico, yahoo(YAHOO_FX)])
    assert res["ok"] and res["fallback"] is True and res["source"] == "YAHOO_UNOFFICIAL"
    assert FxRate.objects.get().provider == "YAHOO_UNOFFICIAL"
    ok = refresh_fx([BanxicoFixProvider("tok", http=fake_http(BANXICO)), yahoo(YAHOO_FX)])
    assert ok["fallback"] is False and ok["source"] == "BANXICO_FIX"
    caido = ProviderError("x", transient=True)
    bad = refresh_fx([BanxicoFixProvider("tok", http=fake_http(caido)), yahoo(caido)])
    assert bad["ok"] is False and "BANXICO_FIX" in bad["error"] and "YAHOO_UNOFFICIAL" in bad["error"]


def test_comando_con_respaldo_avisa_que_no_es_oficial(db, monkeypatch):
    from modules.pricing.management.commands import refresh_market_data as cmd

    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    monkeypatch.delenv("BANXICO_TOKEN", raising=False)
    monkeypatch.setattr(cmd, "YahooProvider", lambda: yahoo(YAHOO_FX))
    out = StringIO()
    call_command("refresh_market_data", "--only", "fx", stdout=out)
    assert "18.523" in out.getvalue() and "RESPALDO no oficial" in out.getvalue()
