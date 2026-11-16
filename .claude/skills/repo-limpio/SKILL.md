---
name: repo-limpio
description: Mantiene el repositorio ligero y sin secretos. Úsala SIEMPRE antes de hacer un commit o un push en este proyecto, y cuando se vaya a agregar un archivo nuevo que no sea código o documentación (entornos virtuales, imágenes, capturas, resultados de pruebas, bases de datos, registros, contraseñas o claves).
---

# Repo limpio (regla del usuario)

El usuario quiere un repositorio pequeño y sin cosas innecesarias. Un solo commit con un entorno virtual (`.venv311`, 27,783
archivos) llevó `.git` a 506 MB y se subió a GitHub: no debe repetirse.

## Nunca se versiona

* **Contraseñas, claves, tokens y `.env`** (solo existen `.env.example` e `infra/.env.prod.example`, vacíos). Tampoco en docs,
  commits, pruebas ni mensajes. Las credenciales de las pruebas e2e son de una base temporal y no valen en la real.
* **Entornos y dependencias**: `.venv*`, `venv*`, `node_modules`, `.next`, `__pycache__`, cachés (`.pytest_cache`, `.ruff_cache`).
* **Resultados de pruebas** (no el código de las pruebas): `test-results/`, `playwright-report/`, trazas (`*.trace.zip`),
  cobertura, capturas de pantalla.
* **Imágenes, videos y binarios** (`png`, `jpg`, `gif`, `webp`, `mp4`, `zip`, `gz`...). Única excepción: iconos mínimos de la app
  (`apps/web/public/**`, `apps/web/app/favicon.ico`).
* **Bases de datos y registros**: `*.sqlite3`, `*.db`, `*.log`, respaldos `.asibak`.
* **Datos reales del usuario**: XLSX/PDF de GBM o Mercado Pago, `imports/`, `evidence/`, `tests/fixtures/real/`.
* **Modelos y artefactos generados**: `mlruns/`, pesos de modelos, descargas de datos, builds.

## Sí se versiona

Código fuente, **código de las pruebas** (pytest y Playwright: el usuario decidió conservarlas), migraciones, documentación de texto,
archivos de dependencias y sus candados (`requirements.txt`, `package-lock.json`), plantillas `.env.example` y fixtures sintéticos
pequeños (menos de 50 KB, datos ficticios).

## Antes de cada commit (obligatorio)

```bash
git status --short                       # ¿hay algo que no esperabas?
git diff --cached --stat                 # ¿cuántos archivos y cuánto pesan?
git diff --cached --name-only | grep -i -E "\.(png|jpe?g|gif|webp|zip|sqlite3?|db|log|pdf|xlsx|env)$|venv|node_modules|test-results"
git diff --cached --name-only -z | xargs -0 -I{} sh -c 'test -f "{}" && test $(wc -c < "{}") -gt 204800 && echo "GRANDE: {}"'
```

Si alguno de los dos `grep`/`xargs` imprime algo, **no hagas el commit**: quítalo del área de preparación (`git restore --staged
<archivo>`), agrégalo a `.gitignore` y vuelve a revisar. Un commit nuevo debería tocar pocas decenas de archivos, no miles.

## Si algo ya se coló

1. Dejar de versionarlo con un commit normal: `git rm -r --cached <ruta>` y añadirlo a `.gitignore` (el archivo sigue en el disco).
2. El historial conserva el peso aunque se borre. Reescribirlo (`git filter-repo`) y hacer `git push --force` cambia los hashes y
   afecta a GitHub: **requiere la autorización explícita del usuario, un respaldo previo (`git bundle`) y conservar las fechas**.
   No hacerlo por iniciativa propia.
3. Si lo que se coló es un secreto, avisar de inmediato: hay que **rotarlo**, porque borrarlo del historial no basta.

## Recordatorios del proyecto

Commits pequeños, en español, sin coautores ni menciones a IA. Push solo cuando el usuario lo pida en ese momento.
