"""Observabilidad mínima y segura: identificador de petición, registros JSON con secretos redactados y sondas de salud.

- Cada petición recibe un `X-Request-ID` (se respeta uno válido del proxy, si no se genera) que aparece en la respuesta y
  en todos los registros de esa petición.
- Los registros nunca deben contener secretos: un filtro enmascara contraseñas, tokens, claves, cookies y cabeceras de
  autorización aunque alguien los escriba por error en un mensaje.
- `/api/v1/health` (viveza, en modules.core.views) y `/api/v1/ready` (base de datos y migraciones) no exigen sesión y no revelan datos."""

import contextvars
import json
import logging
import re
import uuid
from datetime import UTC, datetime

from django.db import OperationalError, connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

_SECRET_KEYS = r"(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|authorization|cookie|csrfmiddlewaretoken|sessionid|csrftoken|access[_-]?key)"
_KV = re.compile(rf"(?i)([\w-]*{_SECRET_KEYS}[\w-]*[\"']?\s*[:=]\s*)(?:Bearer\s+)?[\"']?[^\s,;&\"'}}\]]+")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_LONG_HEX = re.compile(r"\b[A-Fa-f0-9]{40,}\b")
REDACTED = "[REDACTADO]"


def redact(text: str) -> str:
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _KV.sub(lambda m: m.group(1) + REDACTED, text)
    return _LONG_HEX.sub(REDACTED, text)  # hashes largos o claves en hexadecimal


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", request_id_var.get()),
            "message": redact(record.getMessage()),
        }
        if record.exc_info:
            out["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(out, ensure_ascii=False)


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get("HTTP_X_REQUEST_ID", "")
        rid = incoming if _VALID_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response["X-Request-ID"] = rid
        return response


def ready(request):
    """Lista para recibir tráfico: la base responde y no hay migraciones pendientes."""
    checks = {"database": False, "migrations": False}
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        checks["database"] = True
        executor = MigrationExecutor(connection)
        checks["migrations"] = not executor.migration_plan(executor.loader.graph.leaf_nodes())
    except OperationalError:
        pass
    ok = all(checks.values())
    return JsonResponse(
        {"status": "ready" if ok else "not_ready", "checks": checks}, status=200 if ok else 503
    )
