import json

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument, InstrumentIdentifier

CSV = (
    "operation,ticker,quantity,price,fees,currency,executed_at,broker_reference\n"
    "BUY,FIC,3.50,100,0,USD,2026-01-05T15:00:00Z,\n"
    "SELL,FIC,1.25,130,1.00,USD,2026-02-05T15:00:00Z,\n"
)


@pytest.fixture
def ctx(db):
    user = get_user_model().objects.create_user("ana", password="x")
    client = APIClient()
    client.force_authenticate(user)
    acc = InvestmentAccount.objects.create(owner=user, name="GBM", broker="GBM_USA")
    ins = Instrument.objects.create(asset_class="STOCK", name="Ficticia")
    InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value="FIC")
    return client, acc


def upload(client, acc, content=CSV):
    f = SimpleUploadedFile("mov.csv", content.encode(), content_type="text/csv")
    return client.post("/api/v1/imports", {"account": acc.id, "file": f}, format="multipart")


def test_flujo_subir_confirmar_exportar(ctx):
    client, acc = ctx
    r = upload(client, acc)
    assert r.status_code == 201, r.content
    assert r.json()["summary"] == {"VALID": 2}
    batch_id = r.json()["id"]

    r = client.post(f"/api/v1/imports/{batch_id}/confirm")
    assert r.status_code == 200, r.content
    assert r.json()["result"]["created"] == 2

    pos = client.get("/api/v1/portfolio/positions").json()
    assert pos[0]["quantity"].startswith("2.25")

    exported = client.get(f"/api/v1/exports?account={acc.id}&format=json")
    assert len(json.loads(exported.content)["transactions"]) == 2
    csv_resp = client.get(f"/api/v1/exports?account={acc.id}")
    assert csv_resp["Content-Type"].startswith("text/csv")


def test_mismo_archivo_dos_veces_da_400(ctx):
    client, acc = ctx
    assert upload(client, acc).status_code == 201
    r = upload(client, acc)
    assert r.status_code == 400 and "ya fue cargado" in str(r.json())


def test_confirmar_dos_veces_da_400(ctx):
    client, acc = ctx
    batch_id = upload(client, acc).json()["id"]
    assert client.post(f"/api/v1/imports/{batch_id}/confirm").status_code == 200
    assert client.post(f"/api/v1/imports/{batch_id}/confirm").status_code == 400


def test_otro_usuario_no_accede(ctx):
    client, acc = ctx
    batch_id = upload(client, acc).json()["id"]
    otro = APIClient()
    otro.force_authenticate(get_user_model().objects.create_user("beto", password="x"))
    assert otro.get(f"/api/v1/imports/{batch_id}").status_code == 404
    assert otro.post(f"/api/v1/imports/{batch_id}/confirm").status_code == 404
    assert upload(otro, acc).status_code == 400
    assert otro.get(f"/api/v1/exports?account={acc.id}").status_code == 400


def test_sin_archivo_y_csv_malo(ctx):
    client, acc = ctx
    assert client.post("/api/v1/imports", {"account": acc.id}, format="multipart").status_code == 400
    assert upload(client, acc, "col1,col2\n1,2\n").status_code == 400
