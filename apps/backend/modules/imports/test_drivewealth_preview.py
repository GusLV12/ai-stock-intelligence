import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.portfolio.models import Transaction

from .drivewealth import parse_activity_text, parse_confirmation_text, preview_drivewealth
from .services import ImportError_


def test_estado_distingue_operaciones_de_sweep_y_filas_desconocidas():
    page = """ACTIVITY
Trade Date Settle Date Currency Activity Type Symbol / Description Quantity Price Amount
11/24/2026 11/25/2026 USD SELL AAA - EMPRESA 2.00000000 10.00 20.00
11/24/2026 11/25/2026 USD DIV AAA detalle desconocido
SWEEP ACTIVITY
11/24/2026 11/25/2026 USD SELL DWBDS - SWEEP 1 1 1
"""
    data = parse_activity_text([page])
    assert len(data["rows"]) == 1 and data["rows"][0]["ticker"] == "AAA"
    assert data["rows"][0]["fees"] is None and data["rows"][0]["fee_estimate"] == "0.0500"
    assert len(data["warnings"]) == 1


def test_confirmacion_con_comisiones_y_cargos_separados():
    page = """Symbol  Security  A/CType  Action  Execution Time  Quantity  Price  Trade Date  Settle Date  Capacity
AAA  EMPRESA INC  M  Sell  2:49:00 PM  2.0  10.0  11/24/2026  11/25/2026  Principal
Principal Amount  $20.00
Commission  $0.05
Transaction Fee  $0.01
Other Fees / Credits  $0.00
Net Amount  $19.94
"""
    data = parse_confirmation_text([page])
    row = data["rows"][0]
    assert row["operation"] == "SELL" and row["commission"] == "0.05"
    assert row["transaction_fee"] == "0.01" and row["net_amount"] == "19.94"


def test_pdf_invalido_se_rechaza():
    with pytest.raises(ImportError_):
        preview_drivewealth(b"no es pdf")


def test_compra_negativa_y_confirmacion_negativa_no_se_interpretan_como_venta_valida():
    statement = "ACTIVITY\n11/24/2026 11/25/2026 USD BUY AAA - EMPRESA -2 10.00 (20.00)"
    data = parse_activity_text([statement])
    assert not data["rows"] and len(data["warnings"]) == 1
    confirmation = """Symbol  Security  A/CType  Action  Execution Time  Quantity  Price  Trade Date  Settle Date  Capacity
AAA  EMPRESA INC  M  Sell  2:49:00 PM  -2.0  10.0  11/24/2026  11/25/2026  Principal
"""
    data = parse_confirmation_text([confirmation])
    assert not data["rows"] and len(data["warnings"]) == 1


def test_dividendo_y_retencion_se_muestran_separados():
    page = """ACTIVITY
11/24/2026 11/25/2026 USD DIV AAA - DIVIDENDO 0 0.00 2.00
11/24/2026 11/25/2026 USD DIVNRA AAA - RETENCION 0 0.00 (0.30)
"""
    data = parse_activity_text([page])
    assert [row["operation"] for row in data["rows"]] == ["DIV", "DIVNRA"]
    assert [row["amount"] for row in data["rows"]] == ["2.00", "-0.30"]
    assert all(row["fee_estimate"] is None for row in data["rows"])


@pytest.mark.django_db
def test_preview_pdf_aisla_cuentas_y_no_crea_transacciones():
    user = get_user_model().objects.create_user("ana")
    account = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_user("otra"))
    response = client.post(
        "/api/v1/imports/drivewealth/preview",
        {"account": account.id, "file": SimpleUploadedFile("x.pdf", b"contenido")},
        format="multipart",
    )
    assert response.status_code == 400
    assert not Transaction.objects.exists()


@pytest.mark.django_db
def test_previsualizacion_multiple_no_guarda_movimientos():
    user = get_user_model().objects.create_user("ana")
    account = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        "/api/v1/imports/drivewealth/preview-many",
        {
            "account": account.id,
            "files": [SimpleUploadedFile("a.pdf", b"mal"), SimpleUploadedFile("b.pdf", b"mal")],
        },
        format="multipart",
    )
    assert response.status_code == 200
    assert len(response.json()["documents"]) == 2
    assert all("error" in item for item in response.json()["documents"])
    assert not Transaction.objects.exists()


@pytest.mark.django_db
def test_multiples_archivos_avisa_duplicado_sin_eliminar_ejecuciones(monkeypatch):
    from . import api as import_api

    def fake_preview(content):
        return {
            "document_type": "statement",
            "rows": [
                {
                    "trade_date": "2020-01-01",
                    "operation": "BUY",
                    "ticker": "AAA",
                    "quantity": "1",
                    "price": "10",
                    "currency": "USD",
                }
            ],
            "warnings": [],
        }

    monkeypatch.setattr(import_api, "preview_drivewealth", fake_preview)
    user = get_user_model().objects.create_user("ana")
    account = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    client = APIClient()
    client.force_authenticate(user)
    result = client.post(
        "/api/v1/imports/drivewealth/preview-many",
        {
            "account": account.id,
            "files": [SimpleUploadedFile("a.pdf", b"mismo"), SimpleUploadedFile("b.pdf", b"mismo")],
        },
        format="multipart",
    ).json()
    assert result["documents"][1]["duplicate_of"] == 0
    assert result["documents"][1]["rows"][0]["possible_duplicate"] is True
    assert not Transaction.objects.exists()
