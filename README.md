# AI Stock Intelligence

Plataforma personal de inteligencia bursátil. Documento rector: [`docs/architecture/AI_Stock_Intelligence_Plan_Maestro_2026.md`](docs/architecture/AI_Stock_Intelligence_Plan_Maestro_2026.md).

Estado: Etapas 1–12 con trabajo entregado; pendientes y reglas del proyecto en [`CLAUDE.md`](CLAUDE.md). Guías de uso en `docs/runbooks/`.

> No es asesoría de inversión: las zonas, valuaciones y señales son estimaciones reproducibles, no predicciones. Nunca opera ni se conecta a un broker.

Hay tres formas de usarla; elige la que corresponda:

| Quiero… | Sección |
|---|---|
| Usarla en mi PC todos los días (la más ligera) | [1. Arranque local](#1-arranque-local-sin-docker) |
| Montarla en un servidor propio con HTTPS | [2. Servidor con Docker](#2-servidor-con-docker) |
| Probar o desarrollar (pruebas, calidad) | [3. Pruebas y calidad](#3-pruebas-y-calidad) |

---

## 1. Arranque local (sin Docker)

Es la forma recomendada en una PC personal: SQLite, sin máquina virtual y con poco consumo de memoria. Requisitos: Python 3.12 y Node 22 o superior.

### 1.1 Instalación (una sola vez)

```bash
# Backend
cd apps/backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser        # tu usuario y contraseña (interactivo)

# Frontend
cd ../web
npm install
```

Configuración opcional en un archivo `.env` en la raíz (copia `.env.example`; **nunca se sube a git**): claves de Twelve Data y
Banxico, y `SEC_CONTACT` para la ingesta de la SEC. Sin claves, los precios usan el respaldo de Yahoo.

### 1.2 Uso diario

Abre **dos terminales** en PowerShell.

**Terminal 1 — backend (puerto 8010):**

```bash
cd apps/backend
.venv\Scripts\Activate.ps1
$env:DJANGO_CSRF_TRUSTED_ORIGINS="http://localhost:3010"
python manage.py runserver 8010
```

**Terminal 2 — pantalla web (puerto 3010).** Para el uso diario conviene compilarla: arranca más rápido y gasta menos memoria que
`npm run dev`.

```bash
cd apps/web
$env:BACKEND_URL="http://127.0.0.1:8010"
npm run build            # solo la primera vez y cuando cambie el código de la pantalla
npm run start -- -p 3010
```

Abre **http://localhost:3010** (usa `localhost`, no `127.0.0.1`: con esa dirección el inicio de sesión falla). Detén con `Ctrl+C`.

Si vas a modificar la pantalla, usa `npm run dev -- -p 3010` en lugar de `build` + `start`.

> Los puertos 3010 y 8010 se eligen porque `applio-desktop.exe` ocupa el 3000 y el 8000 en esta PC. Si los tuyos están libres puedes
> usar los habituales, pero mantén `DJANGO_CSRF_TRUSTED_ORIGINS` y `BACKEND_URL` coherentes con los puertos que elijas.
> `BACKEND_URL` se fija cuando Next arranca o compila.

### 1.3 Actualizar datos

Con el backend instalado (en `apps/backend`, con el entorno virtual activo):

```bash
python manage.py refresh_market_data                   # precios y tipo de cambio
python manage.py refresh_price_history --range 5y      # historial diario (gráficas, riesgo, liquidez)
python manage.py ingest_sec_financials --held          # estados financieros de la SEC (requiere SEC_CONTACT)
python manage.py backup_db                             # respaldo cifrado (ver docs/runbooks/despliegue-remoto.md)
```

### 1.4 Base de demostración (datos ficticios)

Para enseñar o probar sin tocar tus datos reales, usa una base aparte:

```bash
cd apps/backend
$env:SQLITE_PATH="demo.sqlite3"
$env:DEMO_PASSWORD="elige-una-contraseña-de-prueba"
python manage.py migrate
python manage.py seed_demo
python manage.py runserver 8010
```

---

## 2. Servidor con Docker

Para publicar el sistema en un servidor Linux propio con dominio, HTTPS automático, PostgreSQL y respaldos programados. **No lo uses
en tu PC de uso diario** (gasta mucha más memoria). Detalles completos, qué está verificado y qué no:
[`docs/runbooks/despliegue-remoto.md`](docs/runbooks/despliegue-remoto.md).

Servicios (`infra/compose.prod.yaml`): `db` (PostgreSQL 17, red interna), `migrate`, `backend`, `web`, `backup` (cada 24 h, cifrado),
`caddy` (HTTPS en 80/443) y, con perfil, `mcp` (MCP remoto con OAuth) e `inference` (Ollama).

### 2.1 Primer despliegue

Requisitos: servidor Linux con Docker y un dominio apuntando a él.

```bash
git clone https://github.com/GusLV12/ai-stock-intelligence.git
cd ai-stock-intelligence
cp infra/.env.prod.example infra/.env.prod     # rellénalo: dominio, claves, contraseña de la base de datos
```

Genera los secretos (no los pegues en chats ni los subas a git; guarda la clave de respaldos también en tu gestor de contraseñas):

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"     # DJANGO_SECRET_KEY y DB_PASSWORD
cd apps/backend && python manage.py generate_backup_key          # BACKUP_KEY
```

Levanta todo y crea tu usuario:

```bash
docker compose -f infra/compose.prod.yaml --env-file infra/.env.prod up -d --build
docker compose -f infra/compose.prod.yaml --env-file infra/.env.prod exec backend python manage.py createsuperuser
```

Verifica en `https://tu-dominio/api/v1/ready` y **haz la prueba de restauración** antes de cargar datos reales.

### 2.2 Operación

```bash
docker compose -f infra/compose.prod.yaml --env-file infra/.env.prod ps                 # estado
docker compose -f infra/compose.prod.yaml --env-file infra/.env.prod logs -f backend    # registros
docker compose -f infra/compose.prod.yaml --env-file infra/.env.prod up -d --build      # actualizar tras un git pull
docker compose -f infra/compose.prod.yaml --env-file infra/.env.prod down               # detener (conserva los datos)
```

Perfiles opcionales: `--profile mcp` (requiere tu proveedor de identidad y las variables `MCP_*`) y `--profile inference` (Ollama).

> Un volumen no es un respaldo: copia el volumen `backups` fuera del servidor. Si el servidor se pierde, se pierden datos y respaldos juntos.

### 2.3 Probarlo en tu PC (opcional)

Para ensayar el despliegue sin dominio, usa `APP_DOMAIN=localhost` y secretos desechables en un archivo `.env` fuera del repositorio.
Caddy emite un certificado interno y necesita los puertos 80 y 443 libres. Conviene darle a Docker Desktop al menos 4 GB y no
ejecutarlo junto con Ollama o Qlib.

### 2.4 PostgreSQL solo (desarrollo)

Perfil mínimo: solo la base de datos, ligada a localhost (`infra/compose.yaml`).

```bash
cp .env.example .env       # define DB_PASSWORD y DB_HOST=localhost
docker compose -f infra/compose.yaml --env-file .env up -d
```

---

## 3. Pruebas y calidad

```bash
cd apps/backend
pytest modules/paper modules/quant        # subconjuntos; la suite completa tarda unos 17 min en una PC de 8 GB
ruff check . && ruff format --check .

cd ../web && npx eslint .

cd ../../tests/e2e                        # recorren la interfaz real con una base temporal ficticia
npm install && npx playwright install chromium
npm test                                  # o: E2E_SLOWMO=350 npx playwright test --headed   (para verlo en el navegador)
```

Más: [`tests/e2e/README.md`](tests/e2e/README.md) y [`docs/runbooks/segunda-pc.md`](docs/runbooks/segunda-pc.md) (preparar otra PC).

## Documentación

* [`CLAUDE.md`](CLAUDE.md): reglas del proyecto, módulos, trampas conocidas y pendientes.
* `docs/runbooks/`: asistente local, Quant Lab, paper trading, SEC EDGAR, despliegue remoto, segunda PC.
* `docs/financial-methodology/`: metodología (por ejemplo `liquidez.md`).
* `apps/mcp_server/README.md`: servidor MCP (solo lectura). `workers/qlib/README.md`: workers de investigación.
