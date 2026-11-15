"""Descarga el historial de cierres diarios (Yahoo Finance, fuente no oficial) de tus emisoras.

Sirve para volatilidad, correlaciones y gráficas. Solo se envía el símbolo. Es idempotente."""

from django.core.management.base import BaseCommand

from modules.pricing.history import refresh_history
from modules.pricing.providers import YahooProvider
from modules.pricing.refresh import held_tickers


class Command(BaseCommand):
    help = "Guarda los cierres diarios de las emisoras con posición abierta."

    def add_arguments(self, parser):
        parser.add_argument(
            "--range", default="1y", choices=sorted(YahooProvider.HISTORY_RANGES), dest="range_"
        )

    def handle(self, *args, **opts):
        n = len(held_tickers())
        self.stdout.write(
            f"Historial: consultando {n} emisoras ({opts['range_']}) con YAHOO_UNOFFICIAL (no oficial)…"
        )
        res = refresh_history(YahooProvider(), opts["range_"])
        total = sum(res["saved"].values())
        self.stdout.write(
            f"Historial: {total} cierres nuevos en {len(res['saved'])} emisoras, {len(res['unchanged'])} sin cambio."
        )
        for ticker, err in res["failed"].items():
            self.stderr.write(f"  {ticker}: {err}")
