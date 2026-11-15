"""Vincula una identidad OAuth (emisor + sujeto `sub` de tu proveedor de identidad) a tus cuentas y scopes, para usar el
MCP remoto. No crea ninguna clave: el token lo emite tu proveedor de identidad y aquí solo se le dan permisos acotados.
Un token jamás da más permisos que los que fijas aquí."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from modules.accounts.models import InvestmentAccount
from modules.mcp_access.services import SCOPES, create_oauth_binding


class Command(BaseCommand):
    help = "Vincula una identidad OAuth a scopes y cuentas explícitos (mínimo privilegio)."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True)
        parser.add_argument(
            "--issuer", required=True, help="URL https del proveedor de identidad (claim iss)."
        )
        parser.add_argument(
            "--subject", required=True, help="Claim sub de tu usuario en el proveedor de identidad."
        )
        parser.add_argument("--name", required=True)
        parser.add_argument(
            "--scopes", required=True, help=f"Separados por coma. Disponibles: {', '.join(SCOPES)}"
        )
        parser.add_argument("--accounts", required=True, help="Nombres de cuenta separados por coma.")
        parser.add_argument("--days", type=int, default=90)

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
            cred = create_oauth_binding(
                user,
                o["issuer"],
                o["subject"],
                o["name"],
                [s.strip() for s in o["scopes"].split(",") if s.strip()],
                accounts,
                o["days"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"Identidad vinculada: {cred.name} (id {cred.id}), vence {cred.expires_at:%Y-%m-%d}. Revócala con `revoke_mcp_key {cred.id}`."
        )
