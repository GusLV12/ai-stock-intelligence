from django.core.management.base import BaseCommand
from django.utils import timezone

from modules.mcp_access.models import McpCredential


class Command(BaseCommand):
    help = "Lista las credenciales MCP (sin claves) y su estado."

    def handle(self, *args, **o):
        now = timezone.now()
        for c in McpCredential.objects.select_related("owner").prefetch_related("accounts").order_by("id"):
            state = "revocada" if c.revoked_at else ("vencida" if c.expires_at <= now else "activa")
            accounts = ", ".join(a.name for a in c.accounts.all())
            used = f"{c.last_used_at:%Y-%m-%d %H:%M}" if c.last_used_at else "nunca"
            self.stdout.write(
                f"[{c.id}] {c.name} · {c.prefix}… · {state} · scopes={','.join(c.scopes)} · "
                f"cuentas={accounts} · vence {c.expires_at:%Y-%m-%d} · último uso {used}"
            )
