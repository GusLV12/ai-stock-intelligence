"""Auditoría automática de superficie: ninguna ruta de la API responde sin sesión salvo las públicas declaradas, y ninguna
respuesta no autenticada filtra datos ni provoca errores 500. Si alguien agrega una ruta sin protección, esta prueba falla."""

import re

import pytest
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver

PUBLICAS = {
    ("GET", "api/v1/health"),
    ("GET", "api/v1/ready"),
    ("GET", "api/v1/auth/csrf"),
    ("POST", "api/v1/auth/login"),  # sin credenciales válidas responde 400/401; se comprueba aparte
}
METODOS = ("get", "post", "put", "patch", "delete")


def _rutas(patterns=None, prefix=""):
    for p in patterns if patterns is not None else get_resolver().url_patterns:
        raw = prefix + str(p.pattern)
        if isinstance(p, URLResolver):
            yield from _rutas(p.url_patterns, raw)
        elif isinstance(p, URLPattern):
            yield raw


def _concreta(ruta: str) -> str:
    ruta = ruta.replace("^", "").replace(
        "$", ""
    )  # rutas generadas por routers de DRF (expresiones regulares)
    ruta = re.sub(r"\(\?P<\w+>[^)]*\)", "1", ruta)
    ruta = re.sub(r"<int:\w+>", "1", ruta)
    ruta = re.sub(r"<\w+:\w+>", "x", ruta)
    ruta = re.sub(r"<\w+>", "x", ruta)
    return "/" + ruta


RUTAS_API = sorted({r for r in _rutas() if r.startswith("api/v1/")})


def test_se_descubrieron_rutas():
    assert (
        len(RUTAS_API) > 50
    )  # si baja de golpe, el recorrido dejó de ver rutas y la auditoría no auditaría nada


@pytest.mark.django_db
@pytest.mark.parametrize("ruta", RUTAS_API)
def test_ninguna_ruta_responde_sin_sesion_salvo_las_publicas(ruta):
    c = Client(enforce_csrf_checks=False)
    for metodo in METODOS:
        if (metodo.upper(), ruta) in PUBLICAS:
            continue
        r = (
            getattr(c, metodo)(_concreta(ruta), data="{}", content_type="application/json")
            if metodo != "get"
            else c.get(_concreta(ruta))
        )
        assert r.status_code in (401, 403, 405), (
            f"{metodo.upper()} /{ruta} respondió {r.status_code} sin sesión"
        )
        assert r.status_code < 500


@pytest.mark.django_db
def test_login_con_credenciales_malas_no_revela_si_el_usuario_existe():
    c = Client()
    token = c.get("/api/v1/auth/csrf").json()["csrf_token"]

    def intentar(usuario):
        return c.post(
            "/api/v1/auth/login",
            {"username": usuario, "password": "mala"},
            content_type="application/json",
            headers={"X-CSRFToken": token},
        )

    a, b = intentar("nadie"), intentar("tampoco")
    assert a.status_code == b.status_code in (400, 401) and a.json() == b.json()


@pytest.mark.django_db
def test_las_respuestas_publicas_no_exponen_datos_internos():
    c = Client()
    for ruta in ("/api/v1/health", "/api/v1/ready", "/api/v1/auth/csrf"):
        cuerpo = c.get(ruta).content.decode().lower()
        for prohibido in ("traceback", "secret", "password", "sqlite", "/users/", "django.db"):
            assert prohibido not in cuerpo, (ruta, prohibido)


@pytest.mark.django_db
def test_los_errores_no_filtran_trazas_ni_rutas(client):
    for ruta in ("/api/v1/no-existe", "/api/v1/portfolio/accounts/999999"):
        r = client.get(ruta)
        assert r.status_code in (401, 403, 404)
        assert b"Traceback" not in r.content and b"C:\\" not in r.content
