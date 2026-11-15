"""Actualiza precios (Twelve Data) y USD/MXN (Banxico FIX). Claves en .env; sin clave, omite ese bloque."""

import os

from django.core.management.base import BaseCommand

from modules.pricing.providers import BanxicoFixProvider, ProviderError, TwelveDataProvider
from modules.pricing.refresh import held_tickers, refresh_fx, refresh_prices


class Command(BaseCommand):
    help = "Actualiza precios de tus emisoras (TWELVE_DATA_API_KEY) y el FIX USD/MXN (BANXICO_TOKEN)."

    def add_arguments(self, parser):
        parser.add_argument("--only", choices=["prices", "fx"], help="Solo un bloque.")

    def handle(self, *args, **opts):
        only = opts.get("only")
        if only in (None, "prices"):
            self._prices()
        if only in (None, "fx"):
            self._fx()

    def _prices(self):
        key = os.getenv("TWELVE_DATA_API_KEY", "").strip()
        if not key:
            self.stdout.write("Precios: omitido (define TWELVE_DATA_API_KEY en .env).")
            return
        per_min = int(os.getenv("TWELVE_DATA_PER_MINUTE", "8") or 8)
        n = len(held_tickers())
        self.stdout.write(f"Precios: consultando {n} emisoras (~{max(n * 60 // per_min, 0)} s por el límite del plan)…")
        try:
            res = refresh_prices(TwelveDataProvider(key, per_minute=per_min))
        except ProviderError as exc:
            self.stderr.write(f"Precios: {exc}")
            return
        self.stdout.write(
            f"Precios: {len(res['updated'])} nuevos, {len(res['unchanged'])} sin cambio, {len(res['failed'])} con error."
        )
        for ticker, err in res["failed"].items():
            self.stderr.write(f"  {ticker}: {err}")

    def _fx(self):
        token = os.getenv("BANXICO_TOKEN", "").strip()
        if not token:
            self.stdout.write("USD/MXN: omitido (define BANXICO_TOKEN en .env).")
            return
        try:
            res = refresh_fx(BanxicoFixProvider(token))
        except ProviderError as exc:
            self.stderr.write(f"USD/MXN: {exc}")
            return
        if res["ok"]:
            self.stdout.write(f"USD/MXN: FIX {res['rate']} ({'nuevo' if res['created'] else 'sin cambio'}).")
        else:
            self.stderr.write(f"USD/MXN: {res['error']}")
