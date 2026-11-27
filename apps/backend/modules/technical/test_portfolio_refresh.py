from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.models import TaxLot
from modules.portfolio.services import record_buy
from modules.pricing.history_models import DailyClose
from modules.pricing.providers import YahooProvider
from modules.technical.models import PortfolioHistoryRun, TechnicalClose
from modules.technical.portfolio_refresh import create_run, execute_run


@pytest.mark.django_db
def test_refresh_aislado_por_usuario_y_control_concurrencia():
    user = get_user_model().objects.create_user("uno")
    other = get_user_model().objects.create_user("otro")
    for owner, ticker in [(user, "AAA"), (other, "BBB")]:
        acc = InvestmentAccount.objects.create(owner=owner, name="GBM", broker="GBM_USA")
        ins = Instrument.objects.create(name=ticker, market="USA (GBM)", currency="USD", asset_class="STOCK")
        InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=ticker)
        record_buy(account=acc, instrument=ins, quantity=Decimal(1), price=Decimal(10), executed_at=datetime(2026, 1, 1, tzinfo=UTC))
    class Provider:
        name = "YAHOO_UNOFFICIAL"
        def _meta(self, symbol):
            return {"symbol": symbol, "currency": "USD", "exchangeName": "NMS"}
        def fetch_history(self, symbol, range_):
            return {"currency": "USD", "bars": [(date(2026, 1, 2), Decimal(11))]}
    run, created = create_run(user)
    assert created and not create_run(user)[1]
    before = list(TaxLot.objects.values())
    execute_run(run.pk, Provider())
    run.refresh_from_db()
    assert run.status == "completed" and run.results[0]["ticker"] == "AAA"
    assert TechnicalClose.objects.count() == 1 and not DailyClose.objects.exists()
    assert before == list(TaxLot.objects.values())
    client = APIClient(); client.force_authenticate(other)
    assert client.get("/api/v1/technical/portfolio-history-refresh").status_code == 200
    assert APIClient().get("/api/v1/technical/portfolio-history-refresh").status_code in (401, 403)


def test_vela_actual_solo_con_cierre_comprobado():
    now = datetime(2026, 11, 23, 23, 30, tzinfo=UTC)
    day = now.date()
    timestamp = int((now - timedelta(hours=8)).timestamp())
    metadata = {"currency": "USD", "gmtoffset": -18000, "currentTradingPeriod": {"regular": {"end": int((now - timedelta(minutes=30)).timestamp())}}}
    provider = YahooProvider(now=lambda: now, include_closed_today=True)
    assert provider._parse_bars("AAA", [timestamp], [10], metadata)["bars"][0][0] == day
    metadata["currentTradingPeriod"]["regular"]["end"] = int((now + timedelta(hours=1)).timestamp())
    from modules.pricing.providers import ProviderError
    with pytest.raises(ProviderError):
        provider._parse_bars("AAA", [timestamp], [10], metadata)


@pytest.mark.django_db
def test_endpoint_inicia_una_sola_corrida_y_no_muestra_otro_usuario(monkeypatch):
    user = get_user_model().objects.create_user("dueno")
    other = get_user_model().objects.create_user("ajeno")
    calls = []
    monkeypatch.setattr("modules.technical.refresh_api.subprocess.Popen", lambda *args, **kwargs: calls.append(args))
    client = APIClient(); client.force_authenticate(user)
    first = client.post("/api/v1/technical/portfolio-history-refresh", {}, format="json")
    assert first.status_code == 202 and len(calls) == 1
    assert client.post("/api/v1/technical/portfolio-history-refresh", {}, format="json").status_code == 409
    client.force_authenticate(other)
    assert client.get("/api/v1/technical/portfolio-history-refresh").content == b""


@pytest.mark.django_db
def test_mercado_incompatible_no_escribe_velas():
    user = get_user_model().objects.create_user("cartera")
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    ins = Instrument.objects.create(name="AAA", market="NMS", currency="USD", asset_class="STOCK")
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    record_buy(account=acc, instrument=ins, quantity=Decimal(1), price=Decimal(10), executed_at=datetime(2026, 1, 1, tzinfo=UTC))
    class Provider:
        name = "YAHOO_UNOFFICIAL"
        def _meta(self, symbol):
            return {"symbol": symbol, "currency": "USD", "exchangeName": "NYQ"}
    run, _ = create_run(user)
    execute_run(run.pk, Provider())
    run.refresh_from_db()
    assert run.status == "partial" and run.results[0]["status"] == "error" and not TechnicalClose.objects.exists()


@pytest.mark.django_db
def test_reintento_conserva_exitos_y_reprocesa_solo_fallos(monkeypatch):
    from modules.pricing.providers import ProviderError
    from modules.technical import portfolio_refresh
    user = get_user_model().objects.create_user("reintento")
    acc = InvestmentAccount.objects.create(owner=user, name="Cuenta", broker="GBM_USA")
    for ticker in ("AAA", "BBB"):
        ins = Instrument.objects.create(name=ticker, market="NMS", currency="USD", asset_class="STOCK")
        InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=ticker)
        record_buy(account=acc, instrument=ins, quantity=Decimal(1), price=Decimal(10), executed_at=datetime(2026, 1, 1, tzinfo=UTC))
    calls = []
    def load(provider, ident, *_args):
        calls.append(ident.value)
        if ident.value == "BBB" and calls.count("BBB") == 1:
            raise ProviderError("Fallo temporal")
        return {"ticker": ident.value, "status": "updated"}
    monkeypatch.setattr(portfolio_refresh, "_load_one", load)
    run, _ = create_run(user)
    execute_run(run.pk, provider=object())
    run.refresh_from_db()
    assert run.status == "partial" and len(run.results) == 2
    PortfolioHistoryRun.objects.filter(pk=run.pk).update(status="running")
    execute_run(run.pk, provider=object())
    run.refresh_from_db()
    assert run.status == "completed" and calls == ["AAA", "BBB", "BBB"]
    assert [x["attempts"] for x in run.results] == [1, 2]


@pytest.mark.django_db
def test_historial_anterior_del_mismo_proveedor_se_conserva_sin_editar_quant():
    from modules.technical.api import _bars_for, _bulk_bars_for
    ins = Instrument.objects.create(name="AAA", market="NMS", currency="USD", asset_class="STOCK")
    for day, close in [(date(2021, 1, 1), 10), (date(2021, 1, 2), 11)]:
        DailyClose.objects.create(instrument=ins, date=day, close=close, currency="USD", provider="YAHOO_UNOFFICIAL")
    for day, close in [(date(2021, 1, 2), 20), (date(2021, 1, 3), 21)]:
        TechnicalClose.objects.create(instrument=ins, date=day, close=close, currency="USD", provider="YAHOO_UNOFFICIAL")
    before = list(DailyClose.objects.values_list("date", "close"))
    bars, provider = _bars_for(ins.id, 10)
    assert provider == "YAHOO_UNOFFICIAL" and [b["close"] for b in bars] == [10.0, 20.0, 21.0]
    assert _bulk_bars_for([ins.id], 10)[ins.id] == (bars, provider)
    assert before == list(DailyClose.objects.values_list("date", "close"))


@pytest.mark.django_db
def test_corrida_sin_pulso_se_muestra_interrumpida_sin_alterar_datos():
    from django.utils import timezone
    user = get_user_model().objects.create_user("sin_pulso")
    run, _ = create_run(user)
    PortfolioHistoryRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(minutes=31))
    client = APIClient(); client.force_authenticate(user)
    assert client.get("/api/v1/technical/portfolio-history-refresh").json()["status"] == "interrupted"


@pytest.mark.django_db
def test_programador_reintenta_parcial_con_limite(monkeypatch):
    from zoneinfo import ZoneInfo

    from django.core.management import call_command
    from django.utils import timezone
    user = get_user_model().objects.create_user("programado")
    acc = InvestmentAccount.objects.create(owner=user, name="Cuenta", broker="GBM_USA")
    ins = Instrument.objects.create(name="AAA", market="NMS", currency="USD", asset_class="STOCK")
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    record_buy(account=acc, instrument=ins, quantity=Decimal(1), price=Decimal(10), executed_at=datetime(2026, 1, 1, tzinfo=UTC))
    local = timezone.now().astimezone(ZoneInfo("America/New_York"))
    target = local.date() if local.hour >= 18 else local.date() - timedelta(days=1)
    run, _ = create_run(user, "scheduled", target)
    PortfolioHistoryRun.objects.filter(pk=run.pk).update(status="partial", results=[{"ticker": "AAA", "status": "error", "attempts": 1}], updated_at=timezone.now() - timedelta(minutes=16))
    calls = []
    monkeypatch.setattr("modules.technical.management.commands.refresh_technical_portfolio.execute_run", lambda pk: calls.append(pk))
    call_command("refresh_technical_portfolio", scheduled=True)
    run.refresh_from_db()
    assert calls == [run.pk] and run.status == "running" and run.results[0]["attempts"] == 1
    PortfolioHistoryRun.objects.filter(pk=run.pk).update(status="partial", results=[{"ticker": "AAA", "status": "error", "attempts": 3}], updated_at=timezone.now() - timedelta(minutes=16))
    call_command("refresh_technical_portfolio", scheduled=True)
    assert calls == [run.pk]


@pytest.mark.django_db
def test_trabajador_antiguo_no_reanima_corrida_interrumpida(monkeypatch):
    user = get_user_model().objects.create_user("u_interrupcion")
    acc = InvestmentAccount.objects.create(owner=user, name="Cuenta", broker="GBM_USA")
    ins = Instrument.objects.create(name="AAA", market="NMS", currency="USD", asset_class="STOCK")
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    record_buy(account=acc, instrument=ins, quantity=Decimal(1), price=Decimal(10), executed_at=datetime(2026, 1, 1, tzinfo=UTC))
    run, _ = create_run(user)
    def interrupted(*args):
        PortfolioHistoryRun.objects.filter(pk=run.pk).update(status="interrupted")
        return {"ticker": "AAA", "status": "updated"}
    monkeypatch.setattr("modules.technical.portfolio_refresh._load_one", interrupted)
    execute_run(run.pk, provider=object())
    run.refresh_from_db()
    assert run.status == "interrupted" and run.results == []
    assert create_run(user)[1] is True


@pytest.mark.django_db
def test_trabajador_antiguo_no_sobrescribe_una_reanudacion(monkeypatch):
    user = get_user_model().objects.create_user("u_reanudacion")
    acc = InvestmentAccount.objects.create(owner=user, name="Cuenta", broker="GBM_USA")
    ins = Instrument.objects.create(name="AAA", market="NMS", currency="USD", asset_class="STOCK")
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    record_buy(account=acc, instrument=ins, quantity=Decimal(1), price=Decimal(10), executed_at=datetime(2026, 1, 1, tzinfo=UTC))
    run, _ = create_run(user)
    renewed = run.started_at + timedelta(seconds=1)
    def resume(*args):
        PortfolioHistoryRun.objects.filter(pk=run.pk).update(started_at=renewed, results=[{"ticker": "NUEVO"}])
        return {"ticker": "AAA", "status": "updated"}
    monkeypatch.setattr("modules.technical.portfolio_refresh._load_one", resume)
    execute_run(run.pk, provider=object())
    run.refresh_from_db()
    assert run.status == "running" and run.results == [{"ticker": "NUEVO"}]


@pytest.mark.django_db
def test_interrupcion_durante_descarga_no_guarda_velas():
    user = get_user_model().objects.create_user("u_sin_velas")
    acc = InvestmentAccount.objects.create(owner=user, name="Cuenta", broker="GBM_USA")
    ins = Instrument.objects.create(name="AAA", market="NMS", currency="USD", asset_class="STOCK")
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="AAA")
    record_buy(account=acc, instrument=ins, quantity=Decimal(1), price=Decimal(10), executed_at=datetime(2026, 1, 1, tzinfo=UTC))
    run, _ = create_run(user)
    class Provider:
        name = "YAHOO_UNOFFICIAL"
        def _meta(self, symbol):
            return {"symbol": symbol, "currency": "USD", "exchangeName": "NMS"}
        def fetch_history(self, symbol, range_):
            PortfolioHistoryRun.objects.filter(pk=run.pk).update(status="interrupted")
            return {"currency": "USD", "bars": [(date(2026, 1, 2), Decimal(11))]}
    execute_run(run.pk, provider=Provider())
    run.refresh_from_db()
    assert run.status == "interrupted" and not TechnicalClose.objects.exists()
