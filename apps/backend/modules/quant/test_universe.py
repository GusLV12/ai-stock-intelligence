from datetime import date
from decimal import Decimal as D
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.pricing.history_models import DailyClose

from .universe import RESEARCH_TICKERS, ensure_universe


def test_la_lista_es_suficiente_y_sin_repetidos():
    assert len(RESEARCH_TICKERS) >= 40 and len(set(RESEARCH_TICKERS)) == len(RESEARCH_TICKERS)
    assert all(t == t.upper() and t.strip() == t for t in RESEARCH_TICKERS)


@pytest.mark.django_db
def test_reutiliza_instrumentos_existentes_y_marca_solo_los_nuevos_como_investigacion():
    mio = Instrument.objects.create(asset_class="STOCK", name="Broadcom", quantity_scale=6)
    InstrumentIdentifier.objects.create(instrument=mio, kind="TICKER", value="AVGO")
    m = ensure_universe(["avgo", "AAPL"])
    assert m["AVGO"].pk == mio.pk and mio.research_only is False  # el de tu cartera no se toca
    assert m["AAPL"].research_only is True and m["AAPL"].market == "US"
    again = ensure_universe(["AVGO", "AAPL"])
    assert again["AAPL"].pk == m["AAPL"].pk and Instrument.objects.count() == 2  # idempotente


@pytest.mark.django_db
def test_los_instrumentos_de_investigacion_no_contaminan_el_catalogo():
    c = APIClient()
    c.force_authenticate(get_user_model().objects.create_user("ana", password="x"))
    mio = Instrument.objects.create(asset_class="STOCK", name="Broadcom", quantity_scale=6)
    InstrumentIdentifier.objects.create(instrument=mio, kind="TICKER", value="AVGO")
    ensure_universe(["AAPL", "MSFT"])
    body = c.get("/api/v1/instruments").json()
    nombres = [i["name"] for i in (body["results"] if isinstance(body, dict) else body)]
    assert nombres == ["Broadcom"]


class FakeYahoo:
    HISTORY_RANGES = {"1mo", "3mo", "6mo", "1y", "2y", "5y"}
    name = "YAHOO_UNOFFICIAL"

    def fetch_history(self, symbol, range_):
        bar = {
            "date": date(2026, 11, 10),
            "close": D("10.0000"),
            "adj_close": D("9.9000"),
            "open": D("9.5"),
            "high": D("10.5"),
            "low": D("9.0"),
            "volume": 100,
        }
        return {"currency": "USD", "bars": [(bar["date"], bar["close"])], "ohlcv": [bar]}


@pytest.mark.django_db
def test_comando_descarga_el_universo_y_es_idempotente(monkeypatch):
    monkeypatch.setattr("modules.quant.management.commands.load_research_universe.YahooProvider", FakeYahoo)
    out = StringIO()
    call_command("load_research_universe", "--tickers", "AAPL,MSFT", "--range", "2y", stdout=out)
    assert "2 cierres nuevos" in out.getvalue() and DailyClose.objects.count() == 2
    assert DailyClose.objects.first().adj_close == D("9.9")
    out = StringIO()
    call_command("load_research_universe", "--tickers", "AAPL,MSFT", stdout=out)
    assert "0 cierres nuevos" in out.getvalue() and DailyClose.objects.count() == 2


@pytest.mark.django_db
def test_dry_run_no_crea_nada():
    out = StringIO()
    call_command("load_research_universe", "--dry-run", stdout=out)
    assert "no se descargó nada" in out.getvalue() and Instrument.objects.count() == 0
