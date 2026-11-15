"""Crea una clave personal para un cliente MCP. La clave se muestra UNA sola vez; guárdala en un lugar
seguro (no en el repositorio). Solo se almacena su hash."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from modules.accounts.models import InvestmentAccount
from modules.mcp_access.services import SCOPES, create_credential


class Command(BaseCommand):
    help = "Crea una clave MCP con scopes y cuentas explícitos (mínimo privilegio)."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="Nombre de usuario dueño de las cuentas.")
        parser.add_argument("--name", required=True, help="Etiqueta, p. ej. 'Claude Code laptop'.")
        parser.add_argument("--scopes", required=True, help=f"Separados por coma. Disponibles: {', '.join(SCOPES)}")
        parser.add_argument("--accounts", required=True, help="Nombres de cuenta separados por coma.")
        parser.add_argument("--days", type=int, default=90, help="Vigencia en días (1-365, por defecto 90).")

    def handle(self, *args, **o):
        user = get_user_model().objects.filter(username=o["user"]).first()
        if user is None:
            raise CommandError("Usuario no encontrado.")
        names = [n.strip() for n in o["accounts"].split(",") if n.strip()]
        accounts = list(InvestmentAccount.objects.filter(owner=user, name__in=names))
        missing = set(names) - {a.name for a in accounts}
        if missing:
            raise CommandError(f"Cuentas no encontradas: {', '.join(sorted(missing))}")
        try:
            cred, key = create_credential(
                user, o["name"], [s.strip() for s in o["scopes"].split(",") if s.strip()], accounts, o["days"]
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Credencial creada: {cred.name} (id {cred.id}), vence {cred.expires_at:%Y-%m-%d}."))
        self.stdout.write("Guarda esta clave ahora; no se volverá a mostrar:")
        self.stdout.write(key)
