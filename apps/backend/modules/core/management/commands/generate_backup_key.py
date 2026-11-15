"""Genera una clave de cifrado para los respaldos y la muestra UNA vez. Guárdala en tu gestor de contraseñas y en la
variable de entorno BACKUP_KEY; no se escribe en ningún archivo. Sin ella los respaldos no se pueden abrir."""

from django.core.management.base import BaseCommand

from modules.core.backup import generate_key


class Command(BaseCommand):
    help = "Genera una clave BACKUP_KEY nueva."

    def handle(self, *args, **opts):
        self.stdout.write(generate_key())
        self.stderr.write(
            "Guárdala ahora en tu gestor de contraseñas: si la pierdes, los respaldos no se pueden recuperar."
        )
