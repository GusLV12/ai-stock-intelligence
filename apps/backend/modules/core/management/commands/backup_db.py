"""Crea un respaldo cifrado de la base de datos y, opcionalmente, conserva solo los N más recientes.
Requiere BACKUP_KEY en el entorno. Verifica el respaldo recién creado (restauración de prueba) antes de dar el OK."""

from django.core.management.base import BaseCommand, CommandError

from modules.core.backup import BackupError, create_backup, prune, verify_backup


class Command(BaseCommand):
    help = "Respaldo cifrado de la base de datos (con verificación inmediata)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dest", required=True, help="Carpeta de destino (idealmente en otro disco o servicio)."
        )
        parser.add_argument(
            "--keep", type=int, default=0, help="Conservar solo los N más recientes (0 = todos)."
        )

    def handle(self, *args, **o):
        try:
            path = create_backup(o["dest"])
            res = verify_backup(path)
            if not res["ok"]:
                path.unlink(missing_ok=True)
                raise CommandError("El respaldo no pasó la verificación: " + "; ".join(res["problems"]))
            removed = prune(o["dest"], o["keep"]) if o["keep"] else []
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"Respaldo verificado: {path} ({res['tables']} tablas, {res['rows']} filas). Eliminados por retención: {len(removed)}."
        )
        if not res.get("deep", True):
            self.stdout.write(
                "Aviso: verificación superficial (PostgreSQL). Haz la prueba completa con restore_db --pg-database."
            )
