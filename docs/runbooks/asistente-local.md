# Asistente local (Qwen3 con Ollama)

El asistente explica tus datos usando las mismas herramientas de lectura que el MCP. **No calcula**: toda cifra
que no aparezca en los resultados de las herramientas se marca como no verificada. Si Ollama está apagado,
`/api/v1/assistant/chat` responde 503 y el resto de la app (dashboard, ventas, MCP) sigue funcionando.

## Privacidad

Las preguntas y los datos que devuelven las herramientas se envían al servidor de Ollama. Por defecto solo se
acepta uno en esta misma máquina (`localhost`); apuntar a otro equipo exige `OLLAMA_ALLOW_REMOTE=true`.

## Instalación (requiere tu aprobación: descarga de software y de un modelo)

1. Instala Ollama desde su sitio oficial (https://ollama.com/download) y deja el servicio iniciado.
2. Descarga el modelo según el equipo (según el plan maestro, sección 10.2):
   - PC de 8 GB: `ollama pull qwen3:1.7b` (~1.4 GB)
   - Equipo de 24 GB: `ollama pull qwen3:4b` (~2.5 GB); `qwen3:8b` (~5 GB) opcional para comparar.
3. Variables opcionales en `.env`: `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT`, `OLLAMA_NUM_CTX`, `OLLAMA_NUM_PREDICT`.

## Qué esperar en una PC de 8 GB

Con ~1 GB de RAM libre, Windows puede quedarse sin memoria. Cierra otras aplicaciones antes de usar el modelo,
empieza con `qwen3:1.7b` y mide: RAM, tokens por segundo y latencia. En CPU una respuesta puede tardar
decenas de segundos. La NPU no se aprovecha automáticamente con Ollama.

## Banco de pruebas

Ejecútalo sobre datos de PRUEBA (modo Demo), no sobre tu cartera real:

```bash
cd apps/backend
SQLITE_PATH=demo.sqlite3 DEMO_PASSWORD=... .venv/Scripts/python manage.py migrate
SQLITE_PATH=demo.sqlite3 DEMO_PASSWORD=... .venv/Scripts/python manage.py seed_demo
SQLITE_PATH=demo.sqlite3 .venv/Scripts/python manage.py eval_assistant --user demo
```

Comprueba que el modelo elija la herramienta correcta, no invente cifras, no use herramientas inexistentes,
deje claro que no es asesor y no obedezca instrucciones de vender. **Un modelo que no apruebe el banco no debe
usarse con tu cartera real.**
