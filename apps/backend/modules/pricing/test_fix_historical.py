from datetime import date

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .providers import BanxicoFixProvider, ProviderError


def test_fix_fecha_exacta_no_sustituye_por_otro_dia():
    def fake_http(url, headers):
        assert url.endswith("/2026-11-24/2026-11-24")
        return {"bmx": {"series": [{"datos": [{"fecha": "24/11/2026", "dato": "18.30"}]}]}}

    rate, observed = BanxicoFixProvider("ejemplo", http=fake_http).fetch_fix_for_date(date(2026, 11, 24))
    assert str(rate) == "18.30" and observed.date() == date(2026, 11, 24)
    with pytest.raises(ProviderError):
        BanxicoFixProvider(
            "ejemplo",
            http=lambda *a, **k: {"bmx": {"series": [{"datos": [{"fecha": "23/11/2026", "dato": "18.30"}]}]}},
        ).fetch_fix_for_date(date(2026, 11, 24))


@pytest.mark.django_db
def test_fix_no_disponible_sin_credencial(monkeypatch):
    monkeypatch.delenv("BANXICO_TOKEN", raising=False)
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_user("ana"))
    response = client.post("/api/v1/market/fx/historical-fix", {"date": "2020-01-01"}, format="json")
    assert response.status_code == 200 and response.json()["available"] is False


@pytest.mark.django_db
def test_fix_consulta_proveedor_y_reutiliza_el_dato(monkeypatch):
    from datetime import datetime
    from decimal import Decimal
    from zoneinfo import ZoneInfo

    monkeypatch.setenv("BANXICO_TOKEN", "prueba")
    calls = []

    def fake_fetch(self, day):
        calls.append(day)
        return Decimal("18.30"), datetime(2020, 1, 1, 12, tzinfo=ZoneInfo("America/Mexico_City"))

    monkeypatch.setattr(BanxicoFixProvider, "fetch_fix_for_date", fake_fetch)
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_user("ana"))
    for _ in range(2):
        response = client.post("/api/v1/market/fx/historical-fix", {"date": "2020-01-01"}, format="json")
        assert response.status_code == 200 and response.json()["available"] is True
    assert calls == [date(2020, 1, 1)]
