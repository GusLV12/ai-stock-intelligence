"""Carga datos FICTICIOS para probar la app (modo Demo). Solo funciona con DJANGO_DEBUG=true.

Úsalo con una base separada (SQLITE_PATH) para no mezclar datos de prueba con los reales.
Credenciales de prueba: DEMO_USERNAME / DEMO_PASSWORD (ver .env.example).
"""

import os
from datetime import UTC, datetime
from decimal import Decimal as D

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from modules.accounts.models import InvestmentAccount
from modules.imports.models import PositionSnapshot, SnapshotLine
from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.services import record_buy, record_sell

# ticker, títulos, costo promedio, precio reportado
DEMO = [
    ("AAA", D("4.5"), D("100.00"), D("112.40")),
    ("BBB", D("12.25"), D("50.00"), D("44.10")),
    ("CCC", D("0.74298378"), D("300.00"), D("352.75")),
    ("DDD", D(30), D("12.00"), D("12.00")),
]


class Command(BaseCommand):
    help = "Crea usuario, cuenta, posiciones y reporte ficticios (solo en modo debug)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo solo corre con DJANGO_DEBUG=true.")
        username = os.getenv("DEMO_USERNAME", "demo")
        password = os.getenv("DEMO_PASSWORD")
        if not password:
            raise CommandError("Define DEMO_PASSWORD (ver .env.example).")
        user, created = get_user_model().objects.get_or_create(username=username)
        if not created:
            raise CommandError(f"El usuario {username!r} ya existe; usa una base nueva.")
        user.set_password(password)
        user.save()
        acc = InvestmentAccount.objects.create(owner=user, name="Cuenta demo", broker="GBM_USA")
        t0 = datetime(2026, 1, 5, 15, tzinfo=UTC)
        snap = PositionSnapshot.objects.create(
            account=acc,
            file_sha256="d" * 64,
            filename="demo.xlsx",
            as_of=datetime(2026, 11, 13, 23, 38, tzinfo=UTC),
            cash_amount=D("25.50"),
        )
        for ticker, qty, avg, price in DEMO:
            ins = Instrument.objects.create(
                asset_class="STOCK", name=f"{ticker} (ficticia)", market="DEMO", quantity_scale=8
            )
            InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=ticker)
            record_buy(account=acc, instrument=ins, quantity=qty + D(1), price=avg, executed_at=t0)
            record_sell(
                account=acc, instrument=ins, quantity=D(1), price=avg + D(10),
                executed_at=datetime(2026, 6, 1, 15, tzinfo=UTC),
            )  # fmt: skip
            SnapshotLine.objects.create(
                snapshot=snap, ticker=ticker, quantity=qty, average_cost=avg, market_price=price,
                market_value=(qty * price).quantize(D("0.01")), raw=[],
            )  # fmt: skip
        self.stdout.write(self.style.SUCCESS(f"Demo lista para el usuario {username!r}."))
