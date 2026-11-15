"""El MCP remoto por HTTP: sin token no entra nadie, un token de otra audiencia tampoco, y se publica el descubrimiento
(RFC 9728) apuntando al proveedor de identidad. Se prueba contra la aplicación ASGI real del SDK."""

import time

import pytest
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.testclient import TestClient

from .oauth import JwtTokenVerifier
from .test_oauth import AUDIENCE, ISSUER, token, verifier

INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "prueba", "version": "1"}},
}  # fmt: skip
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Host": "mcp.example.com",
}


@pytest.fixture(scope="module")
def client():
    server = MCPServer(
        "asi-prueba", token_verifier=verifier(),
        auth=AuthSettings(issuer_url=ISSUER, resource_server_url=AUDIENCE, required_scopes=[], validate_token_resource=True),
    )  # fmt: skip

    @server.tool()
    async def ping() -> str:
        return "pong"

    app = server.streamable_http_app(
        host="mcp.example.com",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=["mcp.example.com"], allowed_origins=[]
        ),
    )
    with TestClient(app, base_url="https://mcp.example.com") as c:
        yield c


def post(client, tok=None, **headers):
    h = {**HEADERS, **headers}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return client.post("/mcp", json=INIT, headers=h)


def test_sin_token_o_con_token_invalido_no_entra_nadie(client):
    r = post(client)
    assert r.status_code == 401 and "resource_metadata" in r.headers.get("www-authenticate", "")
    assert post(client, "no.es.jwt").status_code == 401
    assert (
        post(client, token(aud="https://otra-api.example.com")).status_code == 401
    )  # token de otra API: no se acepta
    assert post(client, token(exp=int(time.time()) - 3600)).status_code == 401
    assert post(client, token(iss="https://evil.example.com")).status_code == 401


def test_con_un_token_valido_el_servidor_responde(client):
    r = post(client, token())
    assert r.status_code == 200, r.text
    assert "asi-prueba" in r.text


def test_se_publica_el_descubrimiento_apuntando_al_proveedor_de_identidad(client):
    r = client.get("/.well-known/oauth-protected-resource/mcp")
    assert r.status_code == 200
    meta = r.json()
    assert meta["resource"].rstrip("/") == AUDIENCE and ISSUER in [
        x.rstrip("/") for x in meta["authorization_servers"]
    ]


def test_la_proteccion_contra_dns_rebinding_rechaza_hosts_ajenos(client):
    r = post(client, token(), Host="evil.example.com")
    assert r.status_code in (400, 403, 421)


def test_el_servidor_remoto_exige_configuracion_completa():
    with pytest.raises(ValueError):
        JwtTokenVerifier("", AUDIENCE, key_resolver=lambda t: None)
