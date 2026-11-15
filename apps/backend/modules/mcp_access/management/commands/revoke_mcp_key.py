from django.core.management.base import BaseCommand, CommandError

from modules.mcp_access.models import McpCredential
from modules.mcp_access.services import revoke


class Command(BaseCommand):
    help = "Revoca una credencial MCP; surte efecto en la siguiente llamada."

    def add_arguments(self, parser):
        parser.add_argument("id", type=int)

    def handle(self, *args, **o):
        cred = McpCredential.objects.filter(pk=o["id"]).first()
        if cred is None:
            raise CommandError("Credencial no encontrada.")
        revoke(cred)
        self.stdout.write(self.style.SUCCESS(f"Credencial {cred.id} ({cred.name}) revocada."))
