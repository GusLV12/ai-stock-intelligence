"""Restaura un respaldo a un archivo SQLite NUEVO. Nunca toca la base en uso: para reemplazarla, detén la aplicación,
verifica el archivo restaurado y cópialo tú."""

from django.core.management.base import BaseCommand, CommandError

from modules.core.backup import BackupError, restore_backup


class Command(BaseCommand):
    help = "Restaura un respaldo cifrado a un archivo nuevo."

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--target", required=True)
        parser.add_argument(
            "--force", action="store_true", help="Reemplazar el destino si ya existe (nunca la base en uso)."
        )

    def handle(self, *args, **o):
        try:
            out = restore_backup(o["file"], o["target"], force=o["force"])
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Restaurado en {out}. Revisa el archivo antes de usarlo.")
