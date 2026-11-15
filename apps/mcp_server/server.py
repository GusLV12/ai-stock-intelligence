"""Servidor MCP (stdio) de AI Stock Intelligence. SOLO LECTURA.

No reimplementa lógica: cada herramienta pasa por modules.mcp_access.services.run_tool, que
reautentica la clave en cada llamada, comprueba scope y cuentas autorizadas, limita el uso y audita.

Uso (la clave se pasa por variable de entorno, nunca por argumentos ni archivos del repo):
    ASI_MCP_KEY=asi_... python apps/mcp_server/server.py
Variables opcionales de base de datos: las mismas del backend (.env, SQLITE_PATH, DB_HOST…).
"""

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from asgiref.sync import sync_to_async
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from modules.mcp_access import tools
from modules.mcp_access.services import McpError, run_tool

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

mcp = MCPServer(
    "ai-stock-intelligence",
    instructions=(
        "Consulta de solo lectura del portafolio del usuario. Los datos son contables y los precios no son "
        "tiempo real; indica siempre la fecha y la fuente de los precios. No es asesoría de inversión. "
        "Esta interfaz no puede comprar, vender ni mover fondos."
    ),
)


async def _call(name: str, scope: str, fn, **params):
    key = os.environ.get("ASI_MCP_KEY")
    try:
        return await sync_to_async(run_tool, thread_sensitive=True)(key, name, scope, fn, params)
    except McpError as exc:
        raise ToolError(str(exc)) from None


@mcp.tool(annotations=READ_ONLY)
async def portfolio_summary() -> dict:
    """Totales del portafolio por moneda (costo, valor, ganancia no realizada y realizada, efectivo),
    tipo de cambio de referencia, rendimiento XIRR cuando es evaluable, y procedencia/antigüedad de los precios.
    Solo cuentas autorizadas para esta credencial. Scope: portfolio:read."""
    return await _call("portfolio_summary", "portfolio:read", tools.portfolio_summary)


@mcp.tool(annotations=READ_ONLY)
async def list_positions() -> dict:
    """Posiciones abiertas: emisora, títulos, costo promedio, precio (con proveedor y antigüedad), valor,
    ganancia no realizada y peso. Solo cuentas autorizadas. Scope: portfolio:read."""
    return await _call("list_positions", "portfolio:read", tools.list_positions)


@mcp.tool(annotations=READ_ONLY)
async def get_transactions(start: str | None = None, end: str | None = None, limit: int = 50) -> dict:
    """Movimientos (compras y ventas) de las cuentas autorizadas en un periodo.
    start y end son fechas ISO 8601 (por defecto, los últimos 90 días); el periodo máximo es de 366 días
    y limit va de 1 a 200. Scope: transactions:read."""
    return await _call("get_transactions", "transactions:read", tools.get_transactions, start=start, end=end, limit=limit)


if __name__ == "__main__":
    mcp.run()
