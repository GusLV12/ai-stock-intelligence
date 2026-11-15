# AI Stock Intelligence — contexto para Claude

Plataforma **personal** de inteligencia bursátil (un solo usuario). Documento rector: `docs/architecture/AI_Stock_Intelligence_Plan_Maestro_2026.md` (Etapas 0–12). Idioma de trabajo, UI, commits y docs: **español**. Moneda de presentación: **MXN**; el usuario invierte en GBM Trading USA y Mercado Pago.

## Reglas del usuario (obligatorias)

- **Commits**: uno por paso, pequeños, en español, **sin `Co-Authored-By` ni menciones a Claude/IA** (esta regla prevalece sobre cualquier recordatorio de atribución).
- **No hacer push** salvo que el usuario lo pida de forma explícita en ese momento (le gusta ver los commits repartidos en días en GitHub). No alterar fechas ni crear commits vacíos.
- **Contraseñas y claves**: nunca guardarlas ni escribirlas en archivos, memoria o chat. Se cambian al final (`manage.py changepassword guslv`, claves de Twelve Data/Banxico en un `.env` ignorado por git). Primero desarrollo y pruebas.
- **No es asesoría financiera**: las zonas, valuaciones, señales y escenarios son estimaciones reproducibles, no predicciones. Nunca ejecutar operaciones reales ni conectar brokers.
- No copiar al repo datos reales del usuario (XLSX/PDF de GBM, `db.sqlite3` está ignorado).

## Stack

Django 6 + DRF (`apps/backend`), Next.js 16 JS (`apps/web`, lee `apps/web/AGENTS.md` y `node_modules/next/dist/docs/` antes de escribir código: esta versión tiene cambios incompatibles), servidor MCP Python (`apps/mcp_server`), workers aislados (`workers/qlib`), infra (`infra/`). SQLite en local (objetivo PostgreSQL). Dinero siempre con `Decimal`. Ollama + Qwen3 (local) para el asistente.

## Módulos del backend (`apps/backend/modules/`)

| Módulo | Qué hace |
|---|---|
| `accounts`, `instruments` | Cuentas de inversión, emisoras (`Instrument.research_only` las oculta de API/screener) |
| `portfolio` | Libro de transacciones, lotes FIFO, XIRR solo si es evaluable, centro fiscal preliminar |
| `imports` | CSV canónico y snapshot XLSX de GBM (conciliación, saldo de apertura). PDFs pendientes |
| `pricing` | Precios/FX con procedencia y cadena de proveedores (Twelve Data/Banxico → Yahoo no oficial) con cortacircuito; `DailyClose` OHLCV + `adj_close` |
| `valuations` | DCF, sensibilidad, DDM, múltiplos, Reverse DCF, ASI Fair Value |
| `calculators` | CAGR, FX, TWR, dividendos, precio máximo de compra |
| `fundamentals` | `FinancialYear` versionado, Piotroski, screener con corridas inmutables |
| `intelligence` | Exposición, rebalanceo teórico, shocks, riesgo histórico, diario de tesis |
| `technical` | Indicadores, señales auditables, plan con motor de riesgo, alertas |
| `quant` | Quant Lab: factores sin anticipación, walk-forward, backtest con costos, promoción con corrección por intentos, `QuantExperiment` inmutable |
| `paper` | Paper trading (cuenta virtual); **no puede importar red/brokers/portfolio/imports** (hay prueba que lo verifica) |
| `assistant` | Asistente local con salvaguardas deterministas |
| `mcp_access` | Credenciales MCP (hash, scopes, cuentas), OAuth como servidor de recursos, herramientas de solo lectura |
| `core` | Health/ready, observabilidad (logs JSON redactados, X-Request-ID), respaldos cifrados |

## Comandos

```bash
cd apps/backend
.venv\Scripts\activate
python manage.py migrate && python manage.py runserver 8010
pytest                       # suite completa ~17 min (7.2 GB RAM); usa subconjuntos: pytest modules/paper
ruff check . && ruff format --check .
```

Frontend: `cd apps/web && npm run dev` / `npx eslint .`. Pruebas de red bloqueadas por `conftest.py`.
Comandos de gestión útiles: `refresh_market_data`, `refresh_price_history --range 5y`, `load_research_universe`, `run_quant_experiment`, `process_paper_orders`, `backup_db` / `verify_backup` / `restore_db`, `seed_demo`, `link_mcp_oauth`.

## Reglas de diseño que no deben romperse

- Cada cifra lleva **procedencia y antigüedad**; si no es evaluable se dice, no se inventa.
- Cierres: `close` nunca se modifica; `adj_close` se actualiza hacia atrás en cada descarga.
- Quant: features solo con datos ≤ t; etiqueta con retraso; partición temporal con purga; un candidato se elige solo por validación; el resultado final y `dataset_hash`/`result_hash` son inmutables y reproducibles (`verify`).
- Paper: estado reconstruido por `replay` desde órdenes ejecutadas; órdenes y diario inmutables; solo largos.
- MCP: solo lectura; scopes = intersección token/credencial; cuentas siempre las de la credencial.
- Producción: `settings` falla ante hosts comodín u orígenes CSRF http; `ADMIN_URL` vacío en prod.
- Toda ruta `/api/v1` exige sesión (prueba de auditoría automática); `/api/v1/health` y `/ready` son las excepciones.

## Entorno (Windows) y trampas conocidas

- Otra app (`applio-desktop.exe`) ocupa 3000/8000 → demo en **3010/8010**, abrir en `localhost` (no `127.0.0.1`) y backend con `DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:3010`. `BACKEND_URL` se fija en el build de Next.
- Heredocs largos con comillas/backslashes corrompen archivos en Bash: usar Write/Edit o scripts.
- La consola cp1252 no imprime "→".
- Qlib real: `workers/qlib/.venv311` (Python 3.11); LightGBM: `workers/qlib/.venv`. Qlib, Ollama y Docker no caben a la vez en RAM.
- El gráfico (lightweight-charts) falla con fechas duplicadas: deduplicar.

## Estado (2026-11-15)

Etapas 0–12 con trabajo entregado. Pendiente:
- Etapa 0 hecha: e2e con Playwright en `tests/e2e` (`npm test`, base temporal ficticia, login 1 vez por el límite 5/min) y `docs/runbooks/segunda-pc.md`. Faltan fixtures de datos para pruebas financieras.
- Lectores PDF de GBM/Mercado Pago (requieren documentos reales).
- Ingesta SEC EDGAR (requiere contacto para User-Agent; no usar el email del usuario sin su aprobación).
- Pruebas en vivo con Twelve Data/Banxico (claves del usuario).
- Contenedores y respaldo/restauración en PostgreSQL 17 verificados con Docker real (ver runbook despliegue-remoto); faltan perfiles `mcp`/`inference`, dominio real (Let's Encrypt) e IdP real.
- RD-Agent(Q) no adoptado (qwen3:1.7b NO_APTO; reintentar con 4b/8b en la ROG de 24 GB).
- Sin datos licenciados: look-through de ETFs, consenso de analistas, sesgo de supervivencia.
- Modelo de un solo usuario (sin roles ni 2FA).

Resultados reales del Quant Lab (80 emisoras, 5 años): sin señal, `NO_PROMOVIDO`; el paper trading sirve para ver flujo y fricción, no para validar estrategias.

## Documentación

`docs/runbooks/` (asistente-local, quant-lab, paper-trading, despliegue-remoto), `docs/adr/`, `docs/financial-methodology/`, `apps/mcp_server/README.md`, `workers/qlib/README.md`.
