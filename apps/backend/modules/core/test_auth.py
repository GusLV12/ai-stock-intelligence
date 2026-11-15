import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("ana", password="clave-larga-123")


def csrf_client():
    client = APIClient(enforce_csrf_checks=True)
    token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    return client, {"HTTP_X_CSRFTOKEN": token}


def test_login_ok_y_sesion(user):
    client, h = csrf_client()
    r = client.post("/api/v1/auth/login", {"username": "ana", "password": "clave-larga-123"}, **h)
    assert r.status_code == 200 and r.json()["username"] == "ana"
    assert client.get("/api/v1/auth/me").json()["username"] == "ana"
    assert client.get("/api/v1/portfolio/summary").status_code == 200


def test_login_incorrecto_no_revela_si_existe(user):
    client, h = csrf_client()
    a = client.post("/api/v1/auth/login", {"username": "ana", "password": "mal"}, **h)
    b = client.post("/api/v1/auth/login", {"username": "nadie", "password": "mal"}, **h)
    assert a.status_code == b.status_code == 400
    assert a.json() == b.json()


def test_login_sin_csrf_se_rechaza(user):
    client = APIClient(enforce_csrf_checks=True)
    r = client.post("/api/v1/auth/login", {"username": "ana", "password": "clave-larga-123"})
    assert r.status_code == 403


def test_limite_de_intentos(user):
    client, h = csrf_client()
    codes = [
        client.post("/api/v1/auth/login", {"username": "ana", "password": "x"}, **h).status_code
        for _ in range(7)
    ]
    assert codes[:5] == [400] * 5 and codes[5] == 429


def test_logout_cierra_sesion(user):
    client, h = csrf_client()
    client.post("/api/v1/auth/login", {"username": "ana", "password": "clave-larga-123"}, **h)
    # Django rota el token CSRF al iniciar sesión: hay que pedir uno nuevo.
    h = {"HTTP_X_CSRFTOKEN": client.get("/api/v1/auth/csrf").json()["csrf_token"]}
    assert client.post("/api/v1/auth/logout", **h).status_code == 204
    assert client.get("/api/v1/auth/me").status_code in (401, 403)


def test_me_exige_autenticacion(db):
    assert APIClient().get("/api/v1/auth/me").status_code in (401, 403)
