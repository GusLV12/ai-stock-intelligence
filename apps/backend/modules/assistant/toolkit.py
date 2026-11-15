"""Catálogo de herramientas del asistente local. Son las MISMAS funciones de lectura que expone el MCP;
el LLM nunca calcula: solo decide qué herramienta llamar y explica sus resultados."""

import json
from types import SimpleNamespace

from modules.accounts.models import InvestmentAccount
from modules.mcp_access import tools
from modules.mcp_access.services import McpError

MAX_RESULT_CHARS = 12_000

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "portfolio_summary",
            "description": (
                "Totales del portafolio por moneda (costo, valor de mercado, ganancia no realizada y realizada, "
                "efectivo), total en MXN de referencia, rendimiento XIRR y fuente/antigüedad de los precios."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_positions",
            "description": (
                "Posiciones abiertas: emisora, títulos, costo promedio, precio con proveedor y antigüedad, "
                "valor, ganancia no realizada y peso en el portafolio."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_transactions",
            "description": (
                "Compras y ventas en un periodo. start y end son fechas ISO (YYYY-MM-DD); "
                "por defecto, los últimos 90 días; máximo 366 días."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "Fecha inicial ISO, p. ej. 2026-01-01"},
                    "end": {"type": "string", "description": "Fecha final ISO (incluye todo el día)"},
                    "limit": {"type": "integer", "description": "Máximo de filas (1-200)"},
                },
            },
        },
    },
]

_FUNCS = {
    "portfolio_summary": (tools.portfolio_summary, set()),
    "list_positions": (tools.list_positions, set()),
    "get_transactions": (tools.get_transactions, {"start", "end", "limit"}),
}


def _scope_for(user):
    """Alcance local: todas las cuentas del usuario (el asistente corre con su sesión, no con una clave MCP)."""
    return SimpleNamespace(owner=user, accounts=InvestmentAccount.objects.filter(owner=user))


def execute_tool(user, name: str, arguments) -> dict:
    """Devuelve {'ok': bool, 'data'|'error': ...}. Nunca lanza: los errores vuelven al modelo como datos."""
    if name not in _FUNCS:
        return {"ok": False, "error": f"Herramienta desconocida: {name}"}
    fn, allowed = _FUNCS[name]
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except ValueError:
            return {"ok": False, "error": "Argumentos no son JSON válido."}
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "Argumentos inválidos."}
    # Modelos pequeños suelen inventar argumentos: se descartan (nunca llegan a la función) en vez de fallar.
    clean = {k: v for k, v in arguments.items() if k in allowed and v is not None}
    try:
        if "limit" in clean:
            clean["limit"] = int(clean["limit"])
        return {"ok": True, "data": fn(_scope_for(user), **clean)}
    except (McpError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}


def to_model_text(result: dict) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + ' …[resultado truncado]"}'
    return text
