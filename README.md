# AI Stock Intelligence

Plataforma personal de inteligencia bursátil. Documento rector: [`docs/architecture/AI_Stock_Intelligence_Plan_Maestro_2026.md`](docs/architecture/AI_Stock_Intelligence_Plan_Maestro_2026.md).

Estado: Etapas 1–12 con trabajo entregado; pendientes y reglas del proyecto en [`CLAUDE.md`](CLAUDE.md). Guías de uso en `docs/runbooks/`.

## Backend (Django + DRF)

```bash
cd apps/backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
# http://127.0.0.1:8000/api/v1/health
pytest
```

## PostgreSQL (Docker)

```bash
cp .env.example .env   # define DB_PASSWORD y DB_HOST=localhost
docker compose -f infra/compose.yaml --env-file .env up -d
```
