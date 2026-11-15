"""Reevalúa las órdenes limitadas pendientes de todas las cuentas virtuales con los precios guardados y vence las
antiguas. No consulta proveedores (corre antes refresh_market_data o refresh_price_history) y no envía nada a ningún broker."""

from django.core.management.base import BaseCommand

from modules.paper.models import PaperAccount
from modules.paper.services import process_pending


class Command(BaseCommand):
    help = "Procesa las órdenes limitadas pendientes del paper trading."

    def handle(self, *args, **opts):
        total = {"filled": 0, "expired": 0, "rejected": 0, "still_pending": 0}
        for acc in PaperAccount.objects.filter(orders__status="PENDING").distinct():
            for k, ids in process_pending(acc).items():
                total[k] += len(ids)
        self.stdout.write(
            f"Paper trading: {total['filled']} ejecutadas, {total['expired']} vencidas, {total['rejected']} rechazadas, {total['still_pending']} siguen pendientes."
        )
