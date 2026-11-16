# Preparar otra PC (por ejemplo la ROG de 24 GB)

Nada de esto usa tus datos reales: las pruebas y la demo trabajan con una base temporal y datos ficticios.

## 1. Requisitos
Git, Python 3.12, Node 22+ (probado con 24). Opcionales: Python 3.11 (Qlib), Ollama (asistente local), Docker (despliegue).

## 2. Clonar e instalar
```bash
git clone https://github.com/GusLV12/ai-stock-intelligence.git
cd ai-stock-intelligence/apps/backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
cd ../web && npm install
```

## 3. Probar que todo funciona
```bash
cd apps/backend && pytest modules/paper modules/quant     # subconjunto rápido; la suite completa tarda
cd tests/e2e && npm install && npx playwright install chromium && npm test
```
Las pruebas e2e levantan solas el backend (8010) y el frontend (3010) con una base temporal en la carpeta temporal del sistema.
Si usas otro Python: `E2E_PYTHON=ruta\python.exe npm test`. Si 3010/8010 están ocupados, ciérralos antes (la prueba no reutiliza servidores).

## 4. Tus datos reales
No viajan por git (`db.sqlite3`, `imports/`, PDF y XLSX están ignorados). Para moverlos usa un respaldo cifrado:
`python manage.py backup_db --dest <carpeta>` en la PC origen (con `BACKUP_KEY` definida en tu terminal; la carpeta puede ser `infraackups` o una USB), copia el `.asibak` y la clave por un canal seguro (nunca por git ni chat) y
`python manage.py restore_db --target ...` en la nueva PC. Ver `docs/runbooks/despliegue-remoto.md`.

## 5. Con más RAM
Con 24 GB caben Qlib y Ollama a la vez: se puede reintentar `eval_quant_llm` con `qwen3:4b/8b` (ver `docs/runbooks/quant-lab.md`).
