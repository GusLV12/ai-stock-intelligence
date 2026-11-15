from django.conf import settings
from django.db import models

from modules.accounts.models import InvestmentAccount


class McpCredential(models.Model):
    """Clave personal opaca para clientes MCP. Solo se guarda el hash SHA-256 (la clave tiene
    256 bits de entropía, por lo que no necesita sal). Mínimo privilegio: scopes y cuentas explícitos."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mcp_credentials"
    )
    name = models.CharField(max_length=100)
    key_hash = models.CharField(max_length=64, unique=True)
    prefix = models.CharField(max_length=12)  # para identificarla en listados; no sirve para autenticar
    scopes = models.JSONField(default=list)
    accounts = models.ManyToManyField(InvestmentAccount, related_name="mcp_credentials", blank=True)
    # Vinculación con una identidad OAuth (MCP remoto): (emisor, sujeto). Si están vacíos es una clave opaca local.
    oauth_issuer = models.CharField(max_length=200, blank=True)
    oauth_subject = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["oauth_issuer", "oauth_subject"],
                condition=~models.Q(oauth_issuer=""),
                name="uniq_oauth_identity",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"


class McpAuditLog(models.Model):
    """Qué herramienta se invocó, con qué resultado y qué parámetros (sin secretos ni resultados)."""

    class Status(models.TextChoices):
        OK = "ok", "ok"
        DENIED = "denied", "denegado"
        AUTH_FAILED = "auth_failed", "autenticación fallida"
        RATE_LIMITED = "rate_limited", "límite de uso"
        ERROR = "error", "error"

    credential = models.ForeignKey(McpCredential, null=True, blank=True, on_delete=models.SET_NULL)
    credential_prefix = models.CharField(max_length=12, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    tool = models.CharField(max_length=60)
    status = models.CharField(max_length=15, choices=Status.choices)
    params = models.JSONField(default=dict)
    detail = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
