import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_health_publico_y_ok():
    r = APIClient().get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}
