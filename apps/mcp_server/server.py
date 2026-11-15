"""Servidor MCP (stdio) de AI Stock Intelligence. SOLO LECTURA.

No reimplementa lógica: cada herramienta pasa por modules.mcp_access.services.run_tool, que
reautentica la clave en cada llamada, comprueba scope y cuentas autorizadas, limita el uso y audita.

Uso local (stdio; la clave se pasa por variable de entorno, nunca por argumentos ni archivos del repo):
    ASI_MCP_KEY=asi_... python apps/mcp_server/server.py
Uso remoto (Streamable HTTP + OAuth, este proceso es solo el SERVIDOR DE RECURSOS; el proveedor de identidad emite
los tokens). Detrás de un proxy con HTTPS:
    ASI_MCP_TRANSPORT=streamable-http MCP_OAUTH_ISSUER=https://idp.example.com MCP_OAUTH_AUDIENCE=https://mcp.example.com/mcp     MCP_ALLOWED_HOSTS=mcp.example.com python apps/mcp_server/server.py
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
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from modules.mcp_access import tools
from modules.mcp_access.services import McpError, run_tool, run_tool_oauth

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

INSTRUCTIONS = (
    "Consulta de solo lectura del portafolio del usuario. Los datos son contables y los precios no son "
    "tiempo real; indica siempre la fecha y la fuente de los precios. No es asesoría de inversión. "
    "Esta interfaz no puede comprar, vender ni mover fondos."
)
REMOTE = os.environ.get("ASI_MCP_TRANSPORT", "stdio") == "streamable-http"


def build_server() -> MCPServer:
    """Servidor local (stdio) o remoto con OAuth. En remoto TODO exige un token válido: no hay modo anónimo."""
    if not REMOTE:
        return MCPServer("ai-stock-intelligence", instructions=INSTRUCTIONS)
    from modules.mcp_access.oauth import JwtTokenVerifier

    issuer, audience = os.environ.get("MCP_OAUTH_ISSUER", ""), os.environ.get("MCP_OAUTH_AUDIENCE", "")
    if not issuer or not audience:
        raise SystemExit(
            "MCP remoto: define MCP_OAUTH_ISSUER y MCP_OAUTH_AUDIENCE (la URL pública de este recurso)."
        )
    verifier = JwtTokenVerifier(issuer, audience, os.environ.get("MCP_OAUTH_JWKS_URL") or None)
    auth = AuthSettings(
        issuer_url=issuer, resource_server_url=audience, required_scopes=[], validate_token_resource=True
    )
    return MCPServer("ai-stock-intelligence", instructions=INSTRUCTIONS, token_verifier=verifier, auth=auth)


mcp = build_server()


async def _call(name: str, scope: str, fn, **params):
    token = get_access_token()
    try:
        if token is not None:  # remoto: identidad OAuth ya verificada por el SDK con nuestro verificador
            return await sync_to_async(run_tool_oauth, thread_sensitive=True)(
                (token.claims or {}).get("iss", ""),
                token.subject or "",
                token.scopes,
                name,
                scope,
                fn,
                params,
            )
        if REMOTE:  # nunca caer a la clave local en modo remoto
            raise McpError("Credencial inválida, vencida o revocada.")
        return await sync_to_async(run_tool, thread_sensitive=True)(
            os.environ.get("ASI_MCP_KEY"), name, scope, fn, params
        )
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
    return await _call(
        "get_transactions", "transactions:read", tools.get_transactions, start=start, end=end, limit=limit
    )


@mcp.tool(annotations=READ_ONLY)
async def calculate_cagr(mode: str, params: dict) -> dict:
    """Calculadoras de rendimiento. mode: historical (initial, final, years | start_date, end_date), projected
    (buy_price, fundamental_initial, growth, years, exit_multiple, dilution?, dividends_per_share?, scenarios?),
    real (nominal, inflation), fx (local_return, fx_start, fx_end), twr (subperiods) o dividends (shares,
    start_price, end_price, dividends, withholding?). Las tasas son decimales (0.10 = 10%). Es un escenario
    condicionado a tus supuestos, no una predicción. No lee tu cartera. Scope: calculators:run."""
    return await _call("calculate_cagr", "calculators:run", tools.calculate_cagr, mode=mode, params=params)


@mcp.tool(annotations=READ_ONLY)
async def calculate_target_buy_price(params: dict) -> dict:
    """Precio máximo de compra: future_value, years, target_return (decimal), dividends_per_share? y, para exigir
    también margen de seguridad, estimated_value y margin_of_safety. Escenario, no predicción.
    No lee tu cartera. Scope: calculators:run."""
    return await _call(
        "calculate_target_buy_price", "calculators:run", tools.calculate_target_buy_price, params=params
    )


@mcp.tool(annotations=READ_ONLY)
async def portfolio_risk(window_days: int = 365) -> dict:
    """Riesgo histórico de las cuentas autorizadas: volatilidad y caída máxima por activo y de la cartera, y correlaciones,
    en una ventana explícita de 60 a 1830 días. Son métricas pasadas, no predicen el riesgo futuro. Scope: portfolio:read."""
    return await _call("portfolio_risk", "portfolio:read", tools.portfolio_risk, window_days=window_days)


@mcp.tool(annotations=READ_ONLY)
async def simulate_portfolio(scenario: dict) -> dict:
    """Simula shocks deterministas sobre las cuentas autorizadas: scenario = {preset?, shocks?: [{target, pct}], fx_usd_mxn_pct?}.
    Devuelve el impacto por posición y total (activo y cambiario por separado). Es un escenario hipotético, no una predicción,
    y no modifica nada. Scope: portfolio:read."""
    return await _call("simulate_portfolio", "portfolio:read", tools.simulate_portfolio, scenario=scenario)


@mcp.tool(annotations=READ_ONLY)
async def screen_undervalued_stocks(params: dict | None = None) -> dict:
    """Screener por reglas explícitas sobre los fundamentales cargados: min_fscore (Piotroski 0-9), min_margin_of_safety,
    require_positive_fcf, max_debt_to_assets, min_revenue_growth, require_fresh_data, as_of. Cada fila trae su evidencia y las
    dimensiones por separado (sin puntaje único). No guarda la corrida. No lee tu cartera. Scope: research:read."""
    return await _call(
        "screen_undervalued_stocks", "research:read", tools.screen_undervalued_stocks, params=params
    )


@mcp.tool(annotations=READ_ONLY)
async def technical_analysis(ticker: str, days: int = 300) -> dict:
    """Indicadores (EMA, RSI, MACD, ATR, Bollinger, volumen relativo), señales con su regla y los valores que las sustentan, y
    antigüedad del dato, para una emisora con historial guardado. Cierres diarios con retraso: no son predicciones ni recomendaciones.
    days va de 30 a 1000. No lee tu cartera. Scope: research:read."""
    return await _call(
        "technical_analysis", "research:read", tools.technical_analysis, ticker=ticker, days=days
    )


def _transport_security():
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = [h for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h]
    if not hosts:
        raise SystemExit(
            "MCP remoto: define MCP_ALLOWED_HOSTS (dominios permitidos, protección contra DNS rebinding)."
        )
    origins = [o for o in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",") if o]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
    )


if __name__ == "__main__":
    if REMOTE:
        mcp.run(
            "streamable-http",
            host=os.environ.get("MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("MCP_PORT", "8765")),
            transport_security=_transport_security(),
        )
    else:
        mcp.run()
