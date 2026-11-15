# Backend Django (ASGI con uvicorn) y MCP remoto. Imagen sin herramientas de desarrollo en ejecución: usuario sin privilegios.
# Construir desde la raíz del repositorio:  docker build -f infra/dockerfiles/backend.Dockerfile -t asi-backend .
# En producción fija la imagen base por digest (python:3.12-slim@sha256:...) y reconstruye con regularidad para recibir parches.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# postgresql-client: pg_dump/pg_restore/createdb para los respaldos.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

WORKDIR /app
COPY apps/backend/requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt

COPY apps/backend /app/apps/backend
COPY apps/mcp_server /app/apps/mcp_server
WORKDIR /app/apps/backend

# Nada de .env ni documentos reales dentro de la imagen (ver .dockerignore); la configuración llega por variables de entorno.
# /backups pertenece a `app`: un volumen nombrado nuevo hereda este dueño (si no, el respaldo falla con PermissionError).
RUN mkdir -p /backups && chown app:app /backups
USER app
EXPOSE 8000
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
