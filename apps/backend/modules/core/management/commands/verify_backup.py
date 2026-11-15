"""Prueba de restauración: descifra un respaldo, lo restaura en un archivo temporal y comprueba integridad y filas."""

from django.core.management.base import BaseCommand, CommandError

from modules.core.backup import BackupError, verify_backup


class Command(BaseCommand):
    help = "Verifica que un respaldo se puede abrir y restaurar."

    def add_arguments(self, parser):
        parser.add_argument("file")

    def handle(self, *args, **o):
        try:
            res = verify_backup(o["file"])
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        if not res["ok"]:
            raise CommandError("Respaldo con problemas: " + "; ".join(res["problems"]))
        self.stdout.write(
            f"OK: respaldo del {res['created_at']} restaurable ({res['tables']} tablas, {res['rows']} filas, {res['migrations']} migraciones)."
        )
