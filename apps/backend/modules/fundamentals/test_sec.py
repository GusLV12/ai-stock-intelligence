"""Ingesta SEC EDGAR con respuestas simuladas (las pruebas bloquean la red): mapeo, valor original, derivados, límites y comando."""

import io
from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy

from . import sec
from .models import FinancialYear
from .services import latest_years


def fact(end, val, filed, start=None, form="10-K"):
    e = {"end": end, "val": val, "filed": filed, "form": form, "fp": "FY", "fy": int(end[:4]), "accn": "x"}
    if start:
        e["start"] = start
    return e


def usd(*entries):
    return {"units": {"USD": list(entries)}}


def facts_payload():
    """Dos años fiscales (2023 y 2024). El 10-K de 2024 trae el 2023 REEXPRESADO como comparativo: el valor original es el de 2023."""
    gaap = {
        "Revenues": usd(
            fact("2023-12-31", 1000, "2024-02-20", "2023-01-01"),
            fact("2024-12-31", 1200, "2025-02-20", "2024-01-01"),
            fact("2023-12-31", 1050, "2025-02-20", "2023-01-01"),  # comparativo reexpresado: NO debe usarse
            fact("2024-03-31", 300, "2024-05-02", "2024-01-01", form="10-Q"),
            fact(
                "2024-09-30", 880, "2025-02-20", "2024-01-01"
            ),  # acumulado de 9 meses dentro del 10-K: no es un año
        ),
        "NetIncomeLoss": usd(
            fact("2023-12-31", 100, "2024-02-20", "2023-01-01"),
            fact("2024-12-31", 150, "2025-02-20", "2024-01-01"),
        ),
        "OperatingIncomeLoss": usd(
            fact("2023-12-31", 130, "2024-02-20", "2023-01-01"),
            fact("2024-12-31", 190, "2025-02-20", "2024-01-01"),
        ),
        "DepreciationDepletionAndAmortization": usd(
            fact("2023-12-31", 40, "2024-02-20", "2023-01-01"),
            fact("2024-12-31", 50, "2025-02-20", "2024-01-01"),
        ),
        "PaymentsToAcquirePropertyPlantAndEquipment": usd(fact("2024-12-31", 70, "2025-02-20", "2024-01-01")),
        "NetCashProvidedByUsedInOperatingActivities": usd(
            fact("2024-12-31", 210, "2025-02-20", "2024-01-01")
        ),
        "Assets": usd(fact("2023-12-31", 2000, "2024-02-20"), fact("2024-12-31", 2400, "2025-02-20")),
        "AssetsCurrent": usd(fact("2024-12-31", 900, "2025-02-20")),
        "LongTermDebtNoncurrent": usd(fact("2024-12-31", 500, "2025-02-20")),
        "LongTermDebtCurrent": usd(fact("2024-12-31", 60, "2025-02-20")),
        "CashAndCashEquivalentsAtCarryingValue": usd(fact("2024-12-31", 300, "2025-02-20")),
        "WeightedAverageNumberOfDilutedSharesOutstanding": {
            "units": {"shares": [fact("2024-12-31", 98_000_000, "2025-02-20", "2024-01-01")]}
        },
    }
    return {"cik": 1, "entityName": "AAA Inc", "facts": {"us-gaap": gaap}}


def por_anio(payload):
    return {r["fiscal_year"]: r for r in sec.annual_rows("aaa", payload)}


def test_toma_el_valor_original_y_la_fecha_de_su_presentacion():
    rows = por_anio(facts_payload())
    assert set(rows) == {2023, 2024}
    assert (
        rows[2023]["revenue"] == "1000" and rows[2023]["published_at"] == "2024-02-20"
    )  # no 1050 (reexpresado)
    assert rows[2024]["revenue"] == "1200" and rows[2024]["published_at"] == "2025-02-20"
    assert rows[2024]["ticker"] == "AAA" and rows[2024]["period_end"] == "2024-12-31"
    assert rows[2024]["currency"] == "USD"


def test_campos_directos_y_derivados_con_su_regla():
    r = por_anio(facts_payload())
    assert r[2024]["net_income"] == "150" and r[2024]["cfo"] == "210" and r[2024]["total_assets"] == "2400"
    assert (
        r[2024]["cash"] == "300" and r[2024]["capex"] == "70" and r[2024]["shares_outstanding"] == "98000000"
    )
    assert r[2024]["ebitda"] == "240"  # utilidad operativa 190 + depreciación y amortización 50
    assert r[2024]["total_debt"] == "560"  # largo plazo 500 + porción corriente 60
    assert "cfo" not in r[2023] and "total_debt" not in r[2023]  # lo que no existe queda ausente, no en cero


def test_el_capex_siempre_es_positivo():
    p = facts_payload()
    p["facts"]["us-gaap"]["PaymentsToAcquirePropertyPlantAndEquipment"]["units"]["USD"][0]["val"] = -70
    assert por_anio(p)[2024]["capex"] == "70"


def test_sin_depreciacion_o_sin_deuda_de_largo_plazo_no_se_deriva():
    p = facts_payload()
    del p["facts"]["us-gaap"]["DepreciationDepletionAndAmortization"]
    del p["facts"]["us-gaap"]["LongTermDebtNoncurrent"]
    r = por_anio(p)[2024]
    assert "ebitda" not in r and "total_debt" not in r


def test_emisora_extranjera_o_sin_10k_no_esta_soportada():
    with pytest.raises(sec.SecNotSupported, match="CSV"):
        sec.annual_rows("TSM", {"facts": {"ifrs-full": {}}})
    solo_q = {
        "facts": {"us-gaap": {"Revenues": usd(fact("2024-03-31", 1, "2024-05-01", "2024-01-01", "10-Q"))}}
    }
    with pytest.raises(sec.SecNotSupported):
        sec.annual_rows("ZZZ", solo_q)
    with pytest.raises(sec.SecNotSupported):
        sec.annual_rows("ZZZ", {})


def test_enero_de_52_53_semanas_cuenta_al_anio_anterior():
    assert sec._fiscal_year(date(2021, 1, 2)) == 2020
    assert sec._fiscal_year(date(2025, 1, 26)) == 2025
    assert sec._fiscal_year(date(2024, 9, 28)) == 2024


# ------------------------------------------------------------------------------------------------- cliente
TICKERS = {
    "0": {"cik_str": 1, "ticker": "AAA", "title": "AAA Inc"},
    "1": {"cik_str": 2, "ticker": "BRK-B", "title": "B"},
}


class FakeHttp:
    def __init__(self, facts=None):
        self.calls, self.facts = [], facts or {}

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        if url == sec.TICKERS_URL:
            return TICKERS
        cik = int(url.rsplit("CIK", 1)[1].split(".")[0])
        if cik not in self.facts:
            raise sec.SecNotSupported("sin datos")
        return self.facts[cik]


def test_sin_contacto_no_se_consulta_nada(monkeypatch):
    monkeypatch.delenv("SEC_CONTACT", raising=False)
    with pytest.raises(sec.SecError, match="SEC_CONTACT"):
        sec.SecClient()
    monkeypatch.setenv("SEC_CONTACT", "sin-arroba")
    with pytest.raises(sec.SecError):
        sec.SecClient()


def test_el_contacto_va_en_el_user_agent_y_hay_pausa_entre_consultas():
    http, pausas = FakeHttp({1: facts_payload()}), []
    c = sec.SecClient("Ana Prueba ana@example.com", http=http, sleep=pausas.append, clock=lambda: 0.0)
    c.company_facts("aaa")
    assert len(http.calls) == 2 and all("ana@example.com" in h["User-Agent"] for _, h in http.calls)
    assert pausas and pausas[0] == pytest.approx(sec.PAUSE_SECONDS)


def test_alias_de_ticker_con_punto_y_ticker_desconocido():
    c = sec.SecClient("a b@c", http=FakeHttp())
    assert c.cik("BRK.B") == 2
    with pytest.raises(sec.SecNotSupported, match="ETF"):
        c.cik("GLDM")


def test_http_real_solo_https_y_solo_servidores_de_la_sec():
    for url in ("http://data.sec.gov/x", "https://example.com/x", "file:///etc/passwd"):
        with pytest.raises(sec.SecError, match="SEC"):
            sec.http_get(url, {})


# ------------------------------------------------------------------------------------------------- comando
@pytest.fixture
def mundo(db):
    def inst(ticker, cls="STOCK"):
        i = Instrument.objects.create(asset_class=cls, name=ticker, quantity_scale=8)
        InstrumentIdentifier.objects.create(instrument=i, kind="TICKER", value=ticker)
        return i

    u = get_user_model().objects.create_user("ana", password="x")
    acc = InvestmentAccount.objects.create(owner=u, name="A", broker="GBM_USA")
    aaa, etf = inst("AAA"), inst("GLDM", "ETF")
    for i in (aaa, etf):
        record_buy(
            account=acc,
            instrument=i,
            quantity=D(1),
            price=D(10),
            executed_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
    return aaa


class Client(sec.SecClient):
    def __init__(self, facts):
        super().__init__("Ana ana@example.com", http=FakeHttp(facts), sleep=lambda s: None)


def correr(client, *args):
    out, err = io.StringIO(), io.StringIO()
    call_command("ingest_sec_financials", *args, client=client, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def test_comando_importa_es_idempotente_y_marca_la_fuente(mundo):
    out, _ = correr(Client({1: facts_payload()}), "--tickers", "AAA")
    assert "2 nuevos" in out and FinancialYear.objects.filter(source="SEC_EDGAR").count() == 2
    out, _ = correr(Client({1: facts_payload()}), "--tickers", "AAA")
    assert "0 nuevos, 0 versiones, 2 sin cambios" in out and FinancialYear.objects.count() == 2
    # con fecha de presentación, el dato solo "existe" desde que fue público (sin sesgo de anticipación)
    assert [f.fiscal_year for f in latest_years(mundo, as_of=date(2024, 6, 1))] == [2023]
    assert [f.fiscal_year for f in latest_years(mundo, as_of=date(2025, 6, 1))] == [2024, 2023]


def test_dry_run_no_guarda(mundo):
    out, _ = correr(Client({1: facts_payload()}), "--tickers", "AAA", "--dry-run")
    assert "simulación" in out and FinancialYear.objects.count() == 0


def test_held_incluye_acciones_con_posicion_y_deja_fuera_etf(mundo):
    out, _ = correr(Client({1: facts_payload()}), "--held")
    assert "AAA:" in out and "GLDM" not in out


def test_omite_no_soportadas_y_desconocidas_sin_romper_el_resto(mundo):
    out, _ = correr(Client({1: facts_payload()}), "--tickers", "AAA,GLDM,NOEXISTE")
    assert "Omitida GLDM" in out and "Omitida NOEXISTE: no existe en instrumentos" in out
    assert FinancialYear.objects.count() == 2


def test_un_fallo_de_datos_no_bloquea_a_las_demas_pero_el_comando_falla(mundo):
    malo = facts_payload()
    malo["facts"]["us-gaap"]["AssetsCurrent"]["units"]["USD"][0]["val"] = (
        99999  # activo circulante > activos totales
    )
    otro = Instrument.objects.create(asset_class="STOCK", name="BRK", quantity_scale=8)
    InstrumentIdentifier.objects.create(instrument=otro, kind="TICKER", value="BRK-B")
    with pytest.raises(CommandError, match="1 emisoras fallaron"):
        correr(Client({1: malo, 2: facts_payload()}), "--tickers", "AAA,BRK-B")
    assert FinancialYear.objects.filter(instrument=otro).count() == 2
    assert FinancialYear.objects.filter(instrument=mundo).count() == 0


def test_validaciones_del_comando(mundo, monkeypatch):
    for args in ((), ("--tickers", "AAA", "--years", "0")):
        with pytest.raises(CommandError):
            correr(Client({}), *args)
    monkeypatch.delenv("SEC_CONTACT", raising=False)
    with pytest.raises(CommandError, match="SEC_CONTACT"):
        call_command("ingest_sec_financials", "--tickers", "AAA")
