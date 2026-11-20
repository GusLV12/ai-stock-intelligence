"""Descarga el historial (cierre real, ajustado por dividendos y velas) del universo de investigación del Quant Lab.

Solo se envían símbolos públicos a Yahoo Finance (fuente no oficial); no se envía nada de tu cartera. Los instrumentos
nuevos quedan marcados `research_only` y no aparecen en tu catálogo, el screener ni los precios. Es idempotente."""

from django.core.management.base import BaseCommand, CommandError

from modules.pricing.history import refresh_history
from modules.pricing.providers import YahooProvider
from modules.quant.models import QuantUniverseMember
from modules.quant.universe import RESEARCH_TICKERS, UNIVERSE_VERSION, ensure_universe


class Command(BaseCommand):
    help = "Carga el universo de investigación y su historial de precios."

    def add_arguments(self, parser):
        parser.add_argument(
            "--range", default="5y", choices=sorted(YahooProvider.HISTORY_RANGES), dest="range_"
        )
        parser.add_argument(
            "--tickers", default="", help="Lista separada por comas; por defecto el universo versionado."
        )
        parser.add_argument("--dry-run", action="store_true", help="Solo muestra qué se haría.")

    def handle(self, *args, **o):
        tickers = [t for t in o["tickers"].split(",") if t.strip()] or RESEARCH_TICKERS
        if len(tickers) > 200:
            raise CommandError("Máximo 200 emisoras por corrida.")
        if o["dry_run"]:
            self.stdout.write(
                f"Universo {UNIVERSE_VERSION}: {len(tickers)} emisoras, rango {o['range_']} (no se descargó nada)."
            )
            return
        try:
            mapping = ensure_universe(tickers)
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(
            f"Universo {UNIVERSE_VERSION}: descargando {len(mapping)} emisoras ({o['range_']}) con YAHOO_UNOFFICIAL (no oficial)..."
        )
        res = refresh_history(YahooProvider(), o["range_"], tickers=mapping)
        for ticker, instrument in mapping.items():
            if ticker in res["saved"] or ticker in res["unchanged"]:
                QuantUniverseMember.objects.get_or_create(instrument=instrument)
        total = sum(res["saved"].values())
        self.stdout.write(
            f"Listo: {total} cierres nuevos o completados en {len(res['saved'])} emisoras, {len(res['unchanged'])} sin cambio."
        )
        for ticker, err in res["failed"].items():
            self.stderr.write(f"  {ticker}: {err}")
        if res["failed"]:
            self.stderr.write("Vuelve a correr el comando: lo ya guardado no se repite.")
