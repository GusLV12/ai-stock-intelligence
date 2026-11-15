# Pruebas de extremo a extremo (Playwright)

Recorren la interfaz real: login, portafolio, todas las pantallas, Quant Lab y paper trading. Levantan su propio backend y frontend con
una base **temporal con datos ficticios** (`seed_demo`); nunca tocan `db.sqlite3`.

```bash
npm install && npx playwright install chromium
npm test
```

* El login tiene un límite de 5 intentos por minuto: se inicia sesión una sola vez (`specs/setup.mjs`) y se reutiliza.
* Las pruebas fallan si hay errores de JavaScript o de consola en las pantallas.
* Variables: `E2E_PYTHON` (intérprete del backend). Puertos fijos 8010 y 3010.
* Las credenciales de `playwright.config.mjs` son de prueba y solo valen para esa base desechable.
