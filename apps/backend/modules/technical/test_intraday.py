from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.pricing.providers import ProviderError, YahooProvider
from modules.pricing.test_fallback import fake_http

from . import intraday
from . import intraday_api as api_mod

NY = ZoneInfo("America/New_York")
DAY = date(2026, 11, 13)  # viernes
CUTOFF = datetime(2026, 11, 13, 21, 30, tzinfo=UTC)  # 16:30 NY: la sesión ya cerró


def ts(h, m, day=DAY):
    return int(datetime.combine(day, time(h, m), tzinfo=NY).timestamp())


def session(prices, day=DAY, volume=1000):
    """Una vela por franja de 5 min desde las 9:30; `prices` = lista de (open, high, low, close)."""
    out = []
    for i, (o, h, low, c) in enumerate(prices):
        start = datetime.combine(day, time(9, 30), tzinfo=NY) + timedelta(minutes=5 * i)
        out.append({"ts": int(start.timestamp()), "open": o, "high": h, "low": low, "close": c, "volume": volume})
    return out


FLAT = (100.0, 101.0, 99.0, 100.0)


def test_higiene_descarta_fuera_de_horario_fuera_de_cuadricula_y_nulos():
    raw = session([FLAT] * 6)
    raw += [{"ts": ts(8, 0), "open": 1, "high": 2, "low": 1, "close": 1, "volume": 5},  # premercado
            {"ts": ts(16, 0), "open": 1, "high": 2, "low": 1, "close": 1, "volume": 5},  # cierre (fuera)
            {"ts": ts(10, 1) + 7, "open": 1, "high": 2, "low": 1, "close": 1, "volume": 5},  # fuera de cuadrícula
            {"ts": ts(10, 5), "open": None, "high": 2, "low": 1, "close": 1, "volume": 5}]  # fmt: skip
    c = intraday.clean(raw, "America/New_York", CUTOFF)
    h = c["hygiene"]
    assert len(c["bars"]) == 6 and c["usable"] is True and c["session"] == DAY
    assert (h["dropped_extended_hours"], h["dropped_off_grid"], h["dropped_null"]) == (2, 1, 1)


def test_solo_velas_completas_al_corte():
    raw = session([FLAT] * 4)  # 9:30 .. 9:45
    cutoff = datetime.combine(DAY, time(9, 47), tzinfo=NY)  # la vela de las 9:45 termina a las 9:50: incompleta
    c = intraday.clean(raw, "America/New_York", cutoff)
    assert len(c["bars"]) == 3 and c["hygiene"]["dropped_incomplete"] == 1


def test_una_franja_faltante_hace_la_sesion_no_usable():
    raw = session([FLAT] * 6)
    del raw[3]  # falta la franja de las 9:45
    c = intraday.clean(raw, "America/New_York", CUTOFF)
    assert c["usable"] is False and c["missing_slots"] == ["09:45"]


def test_si_no_empieza_a_las_9_30_no_es_usable_y_la_ultima_vela_duplicada_gana():
    raw = session([FLAT] * 5)[1:]  # sin la vela de las 9:30
    assert intraday.clean(raw, "America/New_York", CUTOFF)["usable"] is False
    dup = session([FLAT] * 3) + [{**session([FLAT])[0], "close": 77.0}]  # misma franja 9:30, llega después
    c = intraday.clean(dup, "America/New_York", CUTOFF)
    assert len(c["bars"]) == 3 and c["bars"][0]["close"] == 77.0


def test_toma_la_sesion_mas_reciente_y_volumen_faltante_cuenta_cero():
    raw = session([FLAT] * 3, day=date(2026, 11, 12)) + session([FLAT] * 3)
    raw[4]["volume"] = None
    c = intraday.clean(raw, "America/New_York", CUTOFF)
    assert c["session"] == DAY and len(c["bars"]) == 3
    assert c["hygiene"]["volume_missing"] == 1 and c["bars"][1]["volume"] == 0.0


def test_sin_velas_utiles():
    assert intraday.clean([], "America/New_York", CUTOFF)["bars"] == []
    assert intraday.clean(session([FLAT]), "America/New_York", datetime(2026, 11, 13, 9, 31, tzinfo=NY))["session"] is None


def test_horario_de_verano_se_maneja_por_zona_no_por_offset_fijo():
    verano = date(2026, 7, 15)  # NY = UTC-4
    raw = session([FLAT] * 3, day=verano)
    c = intraday.clean(raw, "America/New_York", datetime(2026, 7, 15, 21, 0, tzinfo=UTC))
    assert len(c["bars"]) == 3 and c["bars"][0]["start"].strftime("%H:%M") == "09:30"


def test_vwap_ejemplo_manual():
    bars = [{"high": 10.0, "low": 8.0, "close": 9.0, "volume": 100.0}, {"high": 12.0, "low": 10.0, "close": 11.0, "volume": 300.0}]
    v = intraday.vwap(bars)
    assert v[0] == pytest.approx(9.0) and v[1] == pytest.approx((9 * 100 + 11 * 300) / 400)  # 10.5
    assert intraday.vwap([{"high": 1, "low": 1, "close": 1, "volume": 0.0}]) == [None]


def bars_for(prices):
    return intraday.clean(session(prices), "America/New_York", CUTOFF)["bars"]


def test_orb_ruptura_alcista_marcada_al_cierre_de_la_vela():
    prices = [(100, 101, 99, 100.5)] * 3 + [(100.5, 101.5, 100.2, 101.2),  # 9:45: no cierra sobre 101 -> si, 101.2 > 101
                                           (101.2, 103, 101, 102.5)]  # fmt: skip
    o = intraday.orb(bars_for(prices), 15)
    assert o["range_high"] == 101 and o["range_low"] == 99 and o["range_end"] == "09:45"
    assert o["hit"]["time"] == "09:50" and o["hit"]["close"] == 101.2  # cierre de la vela 9:45-9:50
    assert o["breakdown"] is None and "cierre >" in o["rule"]


def test_orb_ruptura_bajista_y_sin_ruptura():
    bajista = [(100, 101, 99, 100)] * 3 + [(100, 100, 97, 98.0)]
    o = intraday.orb(bars_for(bajista), 15)
    assert o["hit"] is None and o["breakdown"]["close"] == 98.0
    assert intraday.orb(bars_for([FLAT] * 6), 15)["hit"] is None


def test_orb_no_toma_rupturas_tardias_ni_con_rango_incompleto():
    plano = [FLAT] * 72  # hasta 15:25
    tarde = [*plano, (100, 120, 100, 119.0), (119, 121, 118, 120.0)]  # rupturas a las 15:30 y 15:35
    o = intraday.orb(bars_for(tarde), 15)
    assert o["hit"] is None and o.get("late") is True  # después de 15:30 no hay entradas
    assert intraday.orb(bars_for([FLAT] * 3), 15)["status"] == "range_not_complete"


@pytest.mark.parametrize("m", [0, -5, 7, 12])
def test_orb_or_minutes_invalido(m):
    with pytest.raises(intraday.IntradayError):
        intraday.orb(bars_for([FLAT] * 6), m)


# ---------- adaptador y API ----------


def yahoo_payload(raw, tz="America/New_York", currency="USD"):
    stamps = [r["ts"] for r in raw]
    q = {k: [r[k] for r in raw] for k in ("open", "high", "low", "close", "volume")}
    return {"chart": {"result": [{"meta": {"currency": currency, "exchangeTimezoneName": tz}, "timestamp": stamps,
                                  "indicators": {"quote": [q]}}], "error": None}}  # fmt: skip


def yahoo(resp):
    return YahooProvider(http=fake_http(resp), sleep=lambda s: None)


def test_fetch_intraday_devuelve_crudo_con_zona_y_pide_5m():
    http = fake_http(yahoo_payload(session([FLAT] * 3)))
    out = YahooProvider(http=http, sleep=lambda s: None).fetch_intraday("BRK.B", "5d")
    assert out["timezone"] == "America/New_York" and len(out["raw"]) == 3 and out["raw"][0]["open"] == D("100.0000")
    assert "BRK-B" in http.calls[0][0] and "interval=5m" in http.calls[0][0] and "range=5d" in http.calls[0][0]


@pytest.mark.parametrize(
    "resp,msg",
    [({"chart": {"result": None, "error": {"code": "x"}}}, "no tiene datos intradía"), ({}, "formato"),
     (yahoo_payload([], tz=""), "zona horaria"), (yahoo_payload([], currency=""), "zona horaria")],
)
def test_fetch_intraday_respuestas_invalidas(resp, msg):
    with pytest.raises(ProviderError, match=msg):
        yahoo(resp).fetch_intraday("AAA", "1d")
    with pytest.raises(ProviderError, match="no soportado"):
        yahoo(yahoo_payload([])).fetch_intraday("AAA", "1y")


@pytest.fixture
def client(db):
    user = get_user_model().objects.create_user("ana", password="x")
    ins = Instrument.objects.create(asset_class="STOCK", name="AAA", quantity_scale=8)
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    c = APIClient()
    c.force_authenticate(user)
    return c


def use(monkeypatch, resp):
    monkeypatch.setattr(api_mod, "get_provider", lambda: yahoo(resp))


def test_api_intradia_con_vwap_orb_e_higiene(client, monkeypatch):
    prices = [(100, 101, 99, 100.5)] * 3 + [(100.5, 101.5, 100.2, 101.2)] * 3
    use(monkeypatch, yahoo_payload(session(prices)))
    d = client.get("/api/v1/technical/intraday?ticker=aaa").json()
    assert d["evaluable"] and d["usable"] is True and len(d["bars"]) == 6 and len(d["vwap"]) == 6
    assert d["orb"]["hit"]["time"] == "09:50" and d["session"] == "2026-11-13"
    assert d["freshness"]["state"] in ("mercado_cerrado", "reciente", "retrasado") and d["freshness"]["age_minutes"] is not None
    assert "no oficial" in d["freshness"]["note"] and "no es una recomendación" in d["note"]


def test_api_marca_sesion_con_huecos(client, monkeypatch):
    raw = session([FLAT] * 6)
    del raw[2]
    use(monkeypatch, yahoo_payload(raw))
    d = client.get("/api/v1/technical/intraday?ticker=AAA").json()
    assert d["usable"] is False and d["missing_slots"] == ["09:40"] and "no se considera usable" in d["note"]


def test_api_sin_velas_utiles(client, monkeypatch):
    use(monkeypatch, yahoo_payload([{"ts": ts(8, 0), "open": 1, "high": 2, "low": 1, "close": 1, "volume": 1}]))
    d = client.get("/api/v1/technical/intraday?ticker=AAA").json()
    assert d["evaluable"] is False and d["hygiene"]["dropped_extended_hours"] == 1


def test_api_errores_del_proveedor_no_rompen_la_app(client, monkeypatch):
    use(monkeypatch, ProviderError("No se pudo conectar", transient=True))
    r = client.get("/api/v1/technical/intraday?ticker=AAA")
    assert r.status_code == 503 and "sigue funcionando" in r.json()["note"]
    use(monkeypatch, {"chart": {"result": None, "error": {"code": "x"}}})
    assert client.get("/api/v1/technical/intraday?ticker=AAA").status_code == 404


@pytest.mark.parametrize("qs", ["", "ticker=ZZZ", "ticker=AAA&or_minutes=x", "ticker=AAA&or_minutes=7"])
def test_api_validaciones(client, monkeypatch, qs):
    use(monkeypatch, yahoo_payload(session([FLAT] * 6)))
    assert client.get(f"/api/v1/technical/intraday?{qs}").status_code == 400


def test_api_limite_de_uso_y_autenticacion(client, monkeypatch):
    use(monkeypatch, yahoo_payload(session([FLAT] * 6)))
    codes = [client.get("/api/v1/technical/intraday?ticker=AAA").status_code for _ in range(7)]
    assert codes[:6] == [200] * 6 and codes[6] == 429
    assert APIClient().get("/api/v1/technical/intraday?ticker=AAA").status_code in (401, 403)
