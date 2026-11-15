"""Actualiza precios y USD/MXN con respaldo.

Cadena de precios: Twelve Data (si hay TWELVE_DATA_API_KEY) -> Yahoo Finance (respaldo no oficial).
Cadena de USD/MXN: Banxico FIX (si hay BANXICO_TOKEN) -> Yahoo Finance (respaldo no oficial).
Sin claves, el respaldo permite que la app funcione. Desactívalo con --no-fallback o YAHOO_FALLBACK=false.
"""

import os

from django.core.management.base import BaseCommand

from modules.pricing.providers import BanxicoFixProvider, TwelveDataProvider, YahooProvider
from modules.pricing.refresh import held_tickers, refresh_fx, refresh_prices


def _fallback_enabled(opts) -> bool:
    if opts.get("no_fallback"):
        return False
    return os.getenv("YAHOO_FALLBACK", "true").strip().lower() not in {"0", "false", "no", "off"}


class Command(BaseCommand):
    help = "Actualiza precios de tus emisoras y el FIX USD/MXN, con respaldo si el proveedor principal falla."

    def add_arguments(self, parser):
        parser.add_argument("--only", choices=["prices", "fx"], help="Solo un bloque.")
        parser.add_argument("--no-fallback", action="store_true", help="No usar Yahoo Finance como respaldo.")

    def handle(self, *args, **opts):
        only = opts.get("only")
        fallback = _fallback_enabled(opts)
        if only in (None, "prices"):
            self._prices(fallback)
        if only in (None, "fx"):
            self._fx(fallback)

    def _prices(self, fallback: bool):
        chain = []
        key = os.getenv("TWELVE_DATA_API_KEY", "").strip()
        per_min = int(os.getenv("TWELVE_DATA_PER_MINUTE", "8") or 8)
        if key:
            chain.append(TwelveDataProvider(key, per_minute=per_min))
        else:
            self.stdout.write("Precios: sin TWELVE_DATA_API_KEY; el proveedor principal se omite.")
        if fallback:
            chain.append(YahooProvider())
        if not chain:
            self.stdout.write("Precios: omitido (sin proveedor principal ni respaldo).")
            return
        names = " -> ".join(p.name for p in chain)
        self.stdout.write(f"Precios: consultando {len(held_tickers())} emisoras con {names}…")
        res = refresh_prices(chain)
        self.stdout.write(
            f"Precios: {len(res['updated'])} nuevos, {len(res['unchanged'])} sin cambio, "
            f"{len(res['failed'])} con error."
        )
        if res["fallback_used"]:
            used = ", ".join(res["fallback_used"])
            self.stdout.write(self.style.WARNING(f"  Fuente de respaldo (no oficial) usada para: {used}."))
        for ticker, err in res["failed"].items():
            self.stderr.write(f"  {ticker}: {err}")

    def _fx(self, fallback: bool):
        chain = []
        token = os.getenv("BANXICO_TOKEN", "").strip()
        if token:
            chain.append(BanxicoFixProvider(token))
        else:
            self.stdout.write("USD/MXN: sin BANXICO_TOKEN; el proveedor principal se omite.")
        if fallback:
            chain.append(YahooProvider())
        if not chain:
            self.stdout.write("USD/MXN: omitido (sin proveedor principal ni respaldo).")
            return
        res = refresh_fx(chain)
        if not res["ok"]:
            self.stderr.write(f"USD/MXN: {res['error']}")
            return
        note = " [RESPALDO no oficial]" if res["fallback"] else ""
        state = "nuevo" if res["created"] else "sin cambio"
        self.stdout.write(f"USD/MXN: {res['rate']} ({state}) vía {res['source']}{note}.")
