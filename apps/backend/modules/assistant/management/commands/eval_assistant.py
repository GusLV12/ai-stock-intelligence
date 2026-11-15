"""Ejecuta el banco de pruebas contra el modelo de Ollama configurado.

Úsalo sobre datos de PRUEBA (modo Demo: SQLITE_PATH=demo.sqlite3 + seed_demo), no sobre tu cartera real:
las preguntas y los datos van al modelo local.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from modules.assistant.evals import evaluate
from modules.assistant.ollama import OllamaClient, OllamaError


class Command(BaseCommand):
    help = "Evalúa el asistente local con un banco de preguntas fijas."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="Usuario cuyos datos se consultan (idealmente el demo).")

    def handle(self, *args, **o):
        user = get_user_model().objects.filter(username=o["user"]).first()
        if user is None:
            raise CommandError("Usuario no encontrado.")
        try:
            client = OllamaClient()
        except OllamaError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Modelo: {client.model} · {client.base_url}")
        try:
            results = evaluate(user, client)
        except OllamaError as exc:
            raise CommandError(str(exc)) from exc
        for r in results:
            mark = self.style.SUCCESS("OK  ") if r["passed"] else self.style.ERROR("FALLA")
            self.stdout.write(f"{mark} {r['id']:<22} {r['seconds']:>6} s  {'; '.join(r['problems'])}")
        passed = sum(r["passed"] for r in results)
        self.stdout.write(f"\n{passed}/{len(results)} casos aprobados.")
        if passed != len(results):
            raise CommandError("Hay casos fallidos: no uses este modelo sin revisar los resultados.")
