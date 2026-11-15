"""Registra como observaciones de precio los datos de reportes de GBM ya importados (idempotente)."""

from django.core.management.base import BaseCommand

from modules.imports.models import PositionSnapshot
from modules.imports.snapshots import ingest_prices


class Command(BaseCommand):
    help = "Crea MarketPrice (GBM_REPORT) desde los reportes de portafolio existentes."

    def handle(self, *args, **options):
        total = sum(ingest_prices(s) for s in PositionSnapshot.objects.all())
        self.stdout.write(self.style.SUCCESS(f"Precios nuevos registrados: {total}"))
