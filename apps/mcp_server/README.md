# Servidor MCP (solo lectura)

Expone tu portafolio a clientes MCP (p. ej. Claude Code) por `stdio`. **No puede comprar, vender ni mover fondos.**

## Herramientas

| Tool | Scope | Qué devuelve |
|---|---|---|
| `portfolio_summary` | `portfolio:read` | Totales por moneda, MXN de referencia, XIRR si es evaluable, procedencia de precios |
| `list_positions` | `portfolio:read` | Posiciones con precio, proveedor y antigüedad |
| `get_transactions` | `transactions:read` | Movimientos en un periodo (máx. 366 días, 200 filas) |
| `calculate_cagr` | `calculators:run` | CAGR histórico/proyectado, retorno real, FX, TWR y dividendos (solo cálculo; no lee tu cartera) |
| `calculate_target_buy_price` | `calculators:run` | Precio máximo de compra por rentabilidad objetivo y margen de seguridad |

Cada credencial tiene scopes y **cuentas** explícitos; el servidor solo ve esas cuentas.

## Crear, ver y revocar una clave

```bash
cd apps/backend
.venv/Scripts/python manage.py create_mcp_key --user guslv --name "Claude Code" --scopes portfolio:read --accounts "GBM Trading USA" --days 90
.venv/Scripts/python manage.py list_mcp_keys
.venv/Scripts/python manage.py revoke_mcp_key <id>
```

La clave (`asi_…`) se muestra **una sola vez**; solo se guarda su hash. La revocación y la expiración
surten efecto en la siguiente llamada. Hay un límite de 60 llamadas por minuto y cada llamada se audita
(`McpAuditLog`: herramienta, estado y parámetros; nunca claves ni resultados).

## Conectarlo a Claude Code

La clave va en una variable de entorno, no en archivos del repositorio:

```bash
claude mcp add asi --env ASI_MCP_KEY=asi_TU_CLAVE -- apps/backend/.venv/Scripts/python apps/mcp_server/server.py
```

## Privacidad

Lo que devuelva una herramienta lo procesa el proveedor del cliente (Claude, ChatGPT…). Concede solo los
scopes y cuentas que quieras exponer. Los precios no son tiempo real; las respuestas indican su fuente y antigüedad.

## Pruebas

`pytest apps/backend/modules/mcp_access` incluye una prueba de integración real que lanza este servidor por
stdio con una base temporal y lo usa con el cliente oficial del SDK.
