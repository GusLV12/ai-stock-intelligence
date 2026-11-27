from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument

from .services import record_buy, record_sell


@pytest.fixture
def ctx(db):
    user = get_user_model().objects.create_user("ana", password="x")
    client = APIClient()
    client.force_authenticate(user)
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    ins = Instrument.objects.create(asset_class="STOCK", name="Ficticia")
    return client, acc, ins


def test_resumen_por_ejercicio_y_sin_calculo_fiscal(ctx):
    client, acc, ins = ctx
    record_buy(account=acc, instrument=ins, quantity=D("3.50"), price=D(100),
               executed_at=datetime(2025, 12, 1, 15, tzinfo=UTC))  # fmt: skip
    record_sell(account=acc, instrument=ins, quantity=D("1.25"), price=D(130), fees=D(1),
                executed_at=datetime(2026, 2, 5, 15, tzinfo=UTC))  # fmt: skip
    s = client.get("/api/v1/tax/years/2026/summary").json()
    assert s["sales_count"] == 1 and s["tax_pending_count"] == 1
    usd = s["by_currency"]["USD"]
    assert usd["gross_proceeds"].startswith("162.5") and usd["realized_pnl_book"].startswith("36.5")
    assert s["unreconciled_manual_sales"] == 1
    assert "No es ganancia fiscal" in s["warning"]
    assert client.get("/api/v1/tax/years/2025/summary").json()["sales_count"] == 0


def test_ejercicio_usa_hora_de_mexico(ctx):
    client, acc, ins = ctx
    record_buy(account=acc, instrument=ins, quantity=D(1), price=D(10),
               executed_at=datetime(2025, 12, 1, tzinfo=UTC))  # fmt: skip
    # 1 ene 2026 02:00 UTC = 31 dic 2025 20:00 en CDMX -> ejercicio 2025
    record_sell(account=acc, instrument=ins, quantity=D(1), price=D(12),
                executed_at=datetime(2026, 1, 1, 2, tzinfo=UTC))  # fmt: skip
    assert client.get("/api/v1/tax/years/2025/summary").json()["sales_count"] == 1
    assert client.get("/api/v1/tax/years/2026/summary").json()["sales_count"] == 0


def test_detalle_de_ventas_marca_pendientes(ctx):
    client, acc, ins = ctx
    record_buy(account=acc, instrument=ins, quantity=D(1), price=D(10),
               executed_at=datetime(2026, 1, 1, tzinfo=UTC))  # fmt: skip
    record_sell(account=acc, instrument=ins, quantity=D(1), price=D(12),
                executed_at=datetime(2026, 3, 1, tzinfo=UTC))  # fmt: skip
    sales = client.get("/api/v1/tax/years/2026/sales").json()
    assert sales[0]["tax_status"] == "fiscal_pendiente"
    assert "tipo de cambio a MXN" in sales[0]["pending"]


def test_filtrar_y_exportar_protege_cuentas_y_campos_csv(ctx):
    import csv
    import io

    client, acc, ins = ctx
    ins.name = "=formula"
    ins.save()
    acc.name = "@cuenta"
    acc.save()
    another = InvestmentAccount.objects.create(owner=acc.owner, name="Otra", broker="GBM_USA")
    outsider = InvestmentAccount.objects.create(
        owner=get_user_model().objects.create_user("otro"), name="Privada", broker="GBM_USA"
    )
    for account in (acc, another):
        record_buy(
            account=account,
            instrument=ins,
            quantity=D(2),
            price=D(100),
            executed_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        record_sell(
            account=account,
            instrument=ins,
            quantity=D(1),
            price=D(90),
            fees=D(1),
            executed_at=datetime(2026, 2, 1, tzinfo=UTC),
        )
    assert client.get(f"/api/v1/tax/years/2026/summary?account={acc.id}").json()["sales_count"] == 1
    sale = client.get(f"/api/v1/tax/years/2026/sales?account={acc.id}").json()[0]
    assert D(sale["realized_pnl_book"]) == D(-11)
    assert D(sale["fees"]) == D(1)
    assert client.get(f"/api/v1/tax/years/2026/sales?account={outsider.id}").status_code == 404
    assert client.get(f"/api/v1/tax/years/2026/export?account={outsider.id}").status_code == 404
    response = client.get(f"/api/v1/tax/years/2026/export?account={acc.id}")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert len(rows) == 4
    assert "No es ganancia fiscal" in rows[1][-1]
    assert rows[3][2:4] == ["'@cuenta", "'=formula"]
    assert D(rows[3][10]) == D(-11)


def test_resumen_separa_monedas_y_marca_apertura(ctx):
    client, acc, usd = ctx
    mxn = Instrument.objects.create(asset_class="STOCK", name="MXN", currency="MXN")
    for ins in (usd, mxn):
        record_buy(
            account=acc,
            instrument=ins,
            quantity=D(2),
            price=D(100),
            executed_at=datetime(2025, 1, 1, tzinfo=UTC),
            origin="OPENING",
        )
        record_sell(
            account=acc,
            instrument=ins,
            quantity=D(1),
            price=D(90),
            fees=D(1),
            executed_at=datetime(2026, 2, 1, tzinfo=UTC),
        )
    summary = client.get("/api/v1/tax/years/2026/summary").json()
    assert set(summary["by_currency"]) == {"USD", "MXN"}
    assert all(D(total["realized_pnl_book"]) == D(-11) for total in summary["by_currency"].values())
    sales = client.get("/api/v1/tax/years/2026/sales").json()
    assert all(any("costo de apertura" in reason for reason in sale["pending"]) for sale in sales)
