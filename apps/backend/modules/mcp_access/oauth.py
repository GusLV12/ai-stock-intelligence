"""Verificación de tokens de acceso OAuth 2.x para el MCP remoto (rol de SERVIDOR DE RECURSOS).

Esta aplicación NO es el servidor de autorización: un proveedor de identidad (Keycloak, Authentik, Auth0, Entra…) autentica
al usuario y emite los tokens; aquí solo se verifican, sin confiar en nada que no esté firmado:
  - firma con las llaves públicas del emisor (JWKS) y SOLO algoritmos asimétricos (RS256/ES256…): se rechazan `none` y los
    HS* para evitar el ataque de confusión de algoritmo;
  - `iss` igual al emisor configurado, `aud` igual a ESTE recurso (evita el "token passthrough": un token emitido para otra
    API no sirve aquí), `exp`/`nbf` vigentes y `sub` presente;
  - los scopes del token se limitan a los que existen aquí; luego `services.run_tool_oauth` los cruza con los de la
    credencial vinculada, de modo que un token jamás da más permisos que los que el dueño autorizó."""

import asyncio
import logging

import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken

from .services import SCOPES

log = logging.getLogger(__name__)
ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "PS256")
LEEWAY_SECONDS = 30


class JwtTokenVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        key_resolver=None,
        algorithms=ALLOWED_ALGORITHMS,
    ):
        if not issuer or not audience:
            raise ValueError("Se necesitan el emisor y la audiencia (este recurso).")
        if not (issuer.startswith(("https://", "http://localhost", "http://127.0.0.1"))):
            raise ValueError("El emisor debe ser https:// (http solo en localhost).")
        if not (jwks_url or key_resolver):
            jwks_url = issuer.rstrip("/") + "/.well-known/jwks.json"
        self.issuer, self.audience = issuer, audience
        self.algorithms = tuple(a for a in algorithms if a in ALLOWED_ALGORITHMS)
        self._resolver = (
            key_resolver
            or PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=5).get_signing_key_from_jwt
        )

    def verify_sync(self, token: str) -> AccessToken | None:
        try:
            alg = jwt.get_unverified_header(token).get("alg")
            if alg not in self.algorithms:
                log.warning("token rechazado: algoritmo no permitido")
                return None
            key = self._resolver(token)
            key = getattr(key, "key", key)
            claims = jwt.decode(
                token, key, algorithms=list(self.algorithms), audience=self.audience, issuer=self.issuer,
                leeway=LEEWAY_SECONDS, options={"require": ["exp", "iss", "aud", "sub"]},
            )  # fmt: skip
        except jwt.PyJWTError as exc:
            log.warning("token rechazado: %s", type(exc).__name__)
            return None
        except Exception as exc:  # noqa: BLE001 - JWKS inalcanzable u otro fallo: nunca aceptar
            log.warning("token no verificable: %s", type(exc).__name__)
            return None
        raw = claims.get("scope", claims.get("scp", ""))
        given = (
            raw.split() if isinstance(raw, str) else [str(x) for x in raw] if isinstance(raw, list) else []
        )
        scopes = sorted(s for s in set(given) if s in SCOPES)
        return AccessToken(
            token=token, client_id=str(claims.get("azp") or claims.get("client_id") or "desconocido"), scopes=scopes,
            expires_at=int(claims["exp"]), resource=self.audience, subject=str(claims["sub"]), claims={"iss": claims["iss"]},
        )  # fmt: skip

    async def verify_token(self, token: str) -> AccessToken | None:
        return await asyncio.to_thread(self.verify_sync, token)
