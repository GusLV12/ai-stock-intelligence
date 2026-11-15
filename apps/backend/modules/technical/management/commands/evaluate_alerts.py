"""Evalúa las alertas activas de todos los usuarios con los datos guardados. No consulta proveedores ni envía avisos:
corre refresh_market_data / refresh_price_history antes si quieres datos nuevos."""

from django.core.management.base import BaseCommand

from modules.technical.alerts import evaluate
from modules.technical.models import PriceAlert


class Command(BaseCommand):
    help = "Dispara las alertas cuya condición ya se cumple."

    def handle(self, *args, **opts):
        active = list(PriceAlert.objects.filter(active=True).select_related("instrument", "user"))
        fired = evaluate(active)
        self.stdout.write(f"Alertas evaluadas: {len(active)} · disparadas: {len(fired)}.")
        for a in fired:
            self.stdout.write(
                f"  [{a.user.get_username()}] {a.instrument.name} {a.kind} {a.signal_id or a.threshold}: {a.triggered_detail}"
            )
