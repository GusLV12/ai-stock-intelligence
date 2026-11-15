# AI Stock Intelligence

Plataforma personal de inteligencia bursátil. Documento rector: [`docs/architecture/AI_Stock_Intelligence_Plan_Maestro_2026.md`](docs/architecture/AI_Stock_Intelligence_Plan_Maestro_2026.md).

Estado: **Etapa 0** (preparación y arquitectura base).

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
