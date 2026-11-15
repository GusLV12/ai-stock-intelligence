"""Autenticación, autorización, límite de uso y auditoría de clientes MCP.

El servidor MCP no reimplementa lógica de negocio: valida la credencial en CADA llamada (así la
revocación y la expiración son inmediatas), comprueba el scope, aplica el límite y registra.
"""

import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

from modules.accounts.models import InvestmentAccount

from .models import McpAuditLog, McpCredential

KEY_PREFIX = "asi_"
RATE_LIMIT_PER_MINUTE = 60
MAX_PARAM_LEN = 100

# Scopes que existen hoy. Solo lectura: no hay scopes de escritura ni de movimiento de fondos.
SCOPES = {
    "portfolio:read": "Resumen y posiciones de las cuentas autorizadas",
    "transactions:read": "Movimientos de las cuentas autorizadas (acotados por periodo)",
    "calculators:run": "Calculadoras de CAGR y precio máximo (solo cálculo; no lee tu cartera)",
}


class McpError(Exception):
    """Error mostrable al cliente. El mensaje es deliberadamente genérico en fallos de autenticación."""


class McpAuthError(McpError):
    pass


class McpPermissionError(McpError):
    pass


class McpRateLimited(McpError):
    pass


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_credential(owner, name: str, scopes, accounts, days: int = 90) -> tuple[McpCredential, str]:
    """Devuelve (credencial, clave en claro). La clave en claro solo existe en este retorno."""
    scopes = sorted(set(scopes))
    unknown = [s for s in scopes if s not in SCOPES]
    if unknown:
        raise ValueError(f"Scopes desconocidos: {', '.join(unknown)}")
    if not scopes:
        raise ValueError("Indica al menos un scope.")
    accounts = list(accounts)
    if not accounts:
        raise ValueError("Indica al menos una cuenta autorizada.")
    if any(a.owner_id != owner.id for a in accounts):
        raise ValueError("Solo puedes autorizar tus propias cuentas.")
    if not 1 <= days <= 365:
        raise ValueError("La vigencia debe estar entre 1 y 365 días.")
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    cred = McpCredential.objects.create(
        owner=owner, name=name, key_hash=hash_key(key), prefix=key[:8], scopes=scopes,
        expires_at=timezone.now() + timedelta(days=days),
    )  # fmt: skip
    cred.accounts.set(accounts)
    return cred, key


def revoke(cred: McpCredential) -> None:
    if cred.revoked_at is None:
        cred.revoked_at = timezone.now()
        cred.save(update_fields=["revoked_at"])


def authenticate(key: str | None) -> McpCredential:
    """Mismo error para clave ausente, desconocida, revocada o vencida (no revela cuál)."""
    generic = McpAuthError("Credencial inválida, vencida o revocada.")
    if not key or not key.startswith(KEY_PREFIX):
        raise generic
    cred = McpCredential.objects.select_related("owner").filter(key_hash=hash_key(key)).first()
    if cred is None or cred.revoked_at is not None or cred.expires_at <= timezone.now():
        raise generic
    if not cred.owner.is_active:
        raise generic
    return cred


def authenticate_oauth(issuer: str, subject: str) -> McpCredential:
    """Busca la credencial vinculada a (emisor, sujeto). Mismo error genérico para cualquier fallo."""
    generic = McpAuthError("Credencial inválida, vencida o revocada.")
    if not issuer or not subject:
        raise generic
    cred = (
        McpCredential.objects.select_related("owner")
        .filter(oauth_issuer=issuer, oauth_subject=subject)
        .first()
    )
    if (
        cred is None
        or cred.revoked_at is not None
        or cred.expires_at <= timezone.now()
        or not cred.owner.is_active
    ):
        raise generic
    return cred


def create_oauth_binding(
    owner, issuer: str, subject: str, name: str, scopes, accounts, days: int = 90
) -> McpCredential:
    """Vincula una identidad OAuth (emisor + sujeto del proveedor de identidad) a tus cuentas y scopes. No genera ninguna
    clave utilizable: el hash guardado corresponde a un secreto aleatorio que se descarta."""
    if not issuer.startswith("https://") and not issuer.startswith("http://localhost"):
        raise ValueError("El emisor debe ser https://.")
    if not subject or len(subject) > 255:
        raise ValueError("Indica el sujeto (sub) del proveedor de identidad.")
    if McpCredential.objects.filter(oauth_issuer=issuer, oauth_subject=subject).exists():
        raise ValueError("Esa identidad ya está vinculada.")
    cred, _ = create_credential(owner, name, scopes, accounts, days)  # valida scopes, cuentas y vigencia
    cred.key_hash = hash_key(
        secrets.token_urlsafe(48)
    )  # la clave opaca generada se invalida: solo sirve OAuth
    cred.prefix = "oauth"
    cred.oauth_issuer, cred.oauth_subject = issuer, subject
    cred.save(update_fields=["key_hash", "prefix", "oauth_issuer", "oauth_subject"])
    return cred


def authorize(cred: McpCredential, scope: str) -> None:
    if scope not in cred.scopes:
        raise McpPermissionError(f"La credencial no tiene el scope {scope}.")


def allowed_account_ids(cred: McpCredential) -> list[int]:
    return list(
        cred.accounts.filter(owner=cred.owner).values_list("id", flat=True)  # nunca cuentas ajenas
    )


def _check_rate(cred: McpCredential) -> None:
    since = timezone.now() - timedelta(minutes=1)
    if McpAuditLog.objects.filter(credential=cred, created_at__gte=since).count() >= RATE_LIMIT_PER_MINUTE:
        raise McpRateLimited("Demasiadas llamadas por minuto; espera un momento.")


def _clean_params(params: dict | None) -> dict:
    """Solo escalares cortos: sin estructuras anidadas ni textos largos."""
    out = {}
    for k, v in (params or {}).items():
        if isinstance(v, bool | int | float) or v is None:
            out[str(k)[:40]] = v
        else:
            out[str(k)[:40]] = str(v)[:MAX_PARAM_LEN]
    return out


def audit(cred, tool: str, status: str, params=None, detail: str = "", prefix: str = "") -> None:
    McpAuditLog.objects.create(
        credential=cred,
        credential_prefix=cred.prefix if cred else prefix,
        owner=cred.owner if cred else None,
        tool=tool[:60], status=status, params=_clean_params(params), detail=detail[:200],
    )  # fmt: skip


def run_tool(key: str | None, tool: str, scope: str, fn, params: dict | None = None):
    """Autentica (clave opaca) -> autoriza -> limita -> ejecuta fn(cred, **params) -> audita. Lanza McpError."""
    params = params or {}
    try:
        cred = authenticate(key)
    except McpAuthError:
        audit(None, tool, McpAuditLog.Status.AUTH_FAILED, params)
        raise
    return _execute(cred, tool, scope, fn, params)


def run_tool_oauth(
    issuer: str, subject: str, token_scopes, tool: str, scope: str, fn, params: dict | None = None
):
    """Igual que run_tool pero para un token OAuth ya verificado (firma, emisor, audiencia y vigencia). El token solo
    identifica a (emisor, sujeto); los permisos reales son la INTERSECCIÓN de los scopes del token y los de la credencial
    vinculada, y las cuentas siempre son las de esa credencial: un token nunca amplía lo que el dueño autorizó."""
    params = params or {}
    try:
        cred = authenticate_oauth(issuer, subject)
    except McpAuthError:
        audit(None, tool, McpAuditLog.Status.AUTH_FAILED, params, prefix="oauth")
        raise
    return _execute(cred, tool, scope, fn, params, token_scopes=set(token_scopes))


def _execute(cred: McpCredential, tool: str, scope: str, fn, params: dict, token_scopes: set | None = None):
    try:
        authorize(cred, scope)
        if token_scopes is not None and scope not in token_scopes:
            raise McpPermissionError(f"El token no incluye el scope {scope}.")
        _check_rate(cred)
    except McpPermissionError as exc:
        audit(cred, tool, McpAuditLog.Status.DENIED, params, str(exc))
        raise
    except McpRateLimited as exc:
        audit(cred, tool, McpAuditLog.Status.RATE_LIMITED, params, str(exc))
        raise
    try:
        result = fn(cred, **params)
    except McpError as exc:
        audit(cred, tool, McpAuditLog.Status.ERROR, params, str(exc))
        raise
    except Exception:  # noqa: BLE001 - intencional: nunca filtrar detalles internos al cliente
        audit(cred, tool, McpAuditLog.Status.ERROR, params, "error interno")
        raise McpError("Error interno al ejecutar la herramienta.") from None
    cred.last_used_at = timezone.now()
    cred.save(update_fields=["last_used_at"])
    audit(cred, tool, McpAuditLog.Status.OK, params)
    return result


def accounts_of(cred: McpCredential):
    return InvestmentAccount.objects.filter(id__in=allowed_account_ids(cred))
