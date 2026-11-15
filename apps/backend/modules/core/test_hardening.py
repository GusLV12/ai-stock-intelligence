import json
import logging
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import Client

from . import observability as obs

BACKEND = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------------------------------------------- redacción
@pytest.mark.parametrize(
    "texto,oculto",
    [
        ("login password=hunter2 ok", "hunter2"),
        ('{"password": "hunter2", "user": "ana"}', "hunter2"),
        ("Authorization: Bearer abcdef1234567890", "abcdef1234567890"),
        ("Bearer abcdef1234567890abcdef", "abcdef1234567890abcdef"),
        ("api_key=sk-live-123456 y token: zzz999", "sk-live-123456"),
        ("TWELVE_DATA_API_KEY=abc123xyz", "abc123xyz"),
        ("cookie=sessionid=abcdef; csrftoken=qwerty", "qwerty"),
        ("hash " + "a1b2c3d4" * 8, "a1b2c3d4a1b2c3d4"),
        ("access_key = 'AKIAIOSFODNN7EXAMPLE'", "AKIAIOSFODNN7EXAMPLE"),
    ],
)
def test_los_secretos_se_enmascaran(texto, oculto):
    out = obs.redact(texto)
    assert oculto not in out and obs.REDACTED in out


def test_texto_normal_no_se_altera():
    s = "Orden 12 ejecutada: AAPL 10 títulos a 100.5"
    assert obs.redact(s) == s


def test_el_registro_json_lleva_id_de_peticion_y_sin_secretos():
    logger = logging.getLogger("prueba.hardening")
    logger.propagate = False
    import io

    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(obs.JsonFormatter())
    h.addFilter(obs.RedactFilter())
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    token = obs.request_id_var.set("req-12345678")
    try:
        logger.info("intento con password=%s", "hunter2")
        try:
            raise ValueError("falló con token=abcd1234")
        except ValueError:
            logger.exception("error")
    finally:
        obs.request_id_var.reset(token)
        logger.removeHandler(h)
    lines = [json.loads(x) for x in buf.getvalue().splitlines()]
    assert (
        lines[0]["request_id"] == "req-12345678"
        and lines[0]["level"] == "INFO"
        and "hunter2" not in lines[0]["message"]
    )
    assert "abcd1234" not in lines[1]["exception"] and "hunter2" not in buf.getvalue()


# ---------------------------------------------------------------------------------------- identificador de petición
@pytest.mark.django_db
def test_cada_respuesta_trae_un_id_y_se_respeta_uno_valido():
    c = Client()
    a = c.get("/api/v1/health")
    assert len(a["X-Request-ID"]) == 32
    b = c.get("/api/v1/health", headers={"X-Request-ID": "abc-12345678"})
    assert b["X-Request-ID"] == "abc-12345678"
    mal = c.get("/api/v1/health", headers={"X-Request-ID": "x\ninyeccion"})  # no se refleja entrada rara
    assert mal["X-Request-ID"] != "x\ninyeccion" and len(mal["X-Request-ID"]) == 32
    assert obs.request_id_var.get() == "-"  # no se filtra entre peticiones


# ------------------------------------------------------------------------------------------------------- sondas
@pytest.mark.django_db
def test_health_y_ready_no_piden_sesion_ni_revelan_datos():
    c = Client()
    assert c.get("/api/v1/health").json() == {"status": "ok"}
    r = c.get("/api/v1/ready")
    assert r.status_code == 200 and r.json() == {
        "status": "ready",
        "checks": {"database": True, "migrations": True},
    }


@pytest.mark.django_db
def test_ready_responde_503_con_migraciones_pendientes(monkeypatch):
    class Falso:
        def __init__(self, *_):
            self.loader = type("L", (), {"graph": type("G", (), {"leaf_nodes": lambda self: []})()})()

        def migration_plan(self, _):
            return [("app", "0002")]

    monkeypatch.setattr(obs, "MigrationExecutor", Falso)
    r = Client().get("/api/v1/ready")
    assert r.status_code == 503 and r.json()["checks"] == {"database": True, "migrations": False}


# ------------------------------------------------------------------------------- configuración de producción real
def _manage(*args, **env):
    base = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "DB_"))}
    base.update({"SQLITE_PATH": str(BACKEND / "prod_check.sqlite3"), "PYTHONIOENCODING": "utf-8", **env})
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=BACKEND,
        env=base,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def prod_env(**over):
    return {
        "DJANGO_DEBUG": "false", "DJANGO_SECRET_KEY": secrets.token_urlsafe(64), "DJANGO_ALLOWED_HOSTS": "asi.example.com",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "https://asi.example.com", "DJANGO_BEHIND_PROXY": "true", **over,
    }  # fmt: skip


def test_check_deploy_de_django_no_reporta_nada_en_produccion():
    r = _manage("check", "--deploy", "--fail-level", "WARNING", **prod_env())
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no issues" in r.stdout + r.stderr


@pytest.mark.parametrize(
    "over,mensaje",
    [
        ({"DJANGO_SECRET_KEY": ""}, "DJANGO_SECRET_KEY"),
        ({"DJANGO_ALLOWED_HOSTS": "*"}, "ALLOWED_HOSTS"),
        ({"DJANGO_ALLOWED_HOSTS": ""}, "ALLOWED_HOSTS"),
        ({"DJANGO_CSRF_TRUSTED_ORIGINS": "http://asi.example.com"}, "https://"),
    ],
)
def test_produccion_se_niega_a_arrancar_con_configuracion_insegura(over, mensaje):
    r = _manage("check", **prod_env(**over))
    assert r.returncode != 0 and mensaje in r.stderr


def test_en_produccion_el_admin_no_existe_y_las_cabeceras_son_seguras():
    codigo = (
        "from django.test import Client\n"
        "c = Client(HTTP_HOST='asi.example.com', secure=True)\n"
        "print('admin', c.get('/admin/', secure=True).status_code)\n"
        "r = c.get('/api/v1/health', secure=True)\n"
        "print('hsts', 'Strict-Transport-Security' in r, 'nosniff', r['X-Content-Type-Options'], 'xfo', r['X-Frame-Options'])\n"
        "print('redirect', Client(HTTP_HOST='asi.example.com').get('/api/v1/health').status_code)\n"
    )
    r = _manage("shell", "-c", codigo, **prod_env())
    assert (
        "admin 404" in r.stdout
        and "hsts True nosniff nosniff xfo DENY" in r.stdout
        and "redirect 301" in r.stdout
    ), r.stdout + r.stderr
    r2 = _manage(
        "shell",
        "-c",
        "from django.test import Client\nprint(Client(HTTP_HOST='asi.example.com', secure=True).get('/ruta-secreta/', secure=True).status_code)",
        **prod_env(DJANGO_ADMIN_URL="ruta-secreta"),
    )
    assert "200" in r2.stdout or "302" in r2.stdout, (
        r2.stdout + r2.stderr
    )  # con ruta propia, el admin responde ahí


def test_en_desarrollo_el_admin_sigue_en_su_ruta(db):
    assert Client().get("/admin/", follow=False).status_code in (200, 302)
