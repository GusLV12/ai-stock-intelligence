"""Descarga estados financieros anuales de SEC EDGAR y los importa (versionados e idempotentes).

Requiere SEC_CONTACT en el entorno (nombre y correo que la SEC exige en el User-Agent; ver modules/fundamentals/sec.py).
    python manage.py ingest_sec_financials --held            # emisoras con posiciones
    python manage.py ingest_sec_financials --tickers AMD,MU --years 8 --dry-run
"""

from django.core.management.base import BaseCommand, CommandError

from modules.fundamentals.sec import SecClient, SecError, SecNotSupported, annual_rows
from modules.fundamentals.services import FundamentalsError, import_financials
from modules.instruments.models import Instrument, InstrumentIdentifier


def held_stock_tickers() -> list[str]:
    ids = Instrument.objects.filter(
        asset_class__in=["STOCK", "ADR"], research_only=False, transactions__isnull=False
    ).distinct()
    return sorted(
        InstrumentIdentifier.objects.filter(kind="TICKER", instrument__in=ids).values_list("value", flat=True)
    )


class Command(BaseCommand):
    stealth_options = ("client",)  # inyección de un cliente simulado en las pruebas
    help = "Importa estados financieros anuales (10-K) desde SEC EDGAR. Requiere SEC_CONTACT."

    def add_arguments(self, parser):
        parser.add_argument("--tickers", default="", help="Lista separada por comas.")
        parser.add_argument(
            "--held", action="store_true", help="Todas las acciones con operaciones registradas."
        )
        parser.add_argument("--years", type=int, default=10)
        parser.add_argument("--dry-run", action="store_true", help="Descarga y valida, pero no guarda.")

    def handle(self, *args, client=None, **o):
        tickers = [t.strip().upper() for t in o["tickers"].split(",") if t.strip()]
        if o["held"]:
            tickers = sorted(set(tickers) | set(held_stock_tickers()))
        if not tickers:
            raise CommandError("Indica --tickers o --held.")
        if not 1 <= o["years"] <= 30:
            raise CommandError("--years debe estar entre 1 y 30.")
        try:
            client = client or SecClient()
        except SecError as exc:
            raise CommandError(str(exc)) from None
        totals = {"created": 0, "new_versions": 0, "unchanged": 0}
        skipped, failed = [], []
        for t in tickers:
            if not InstrumentIdentifier.objects.filter(kind="TICKER", value__iexact=t).exists():
                skipped.append((t, "no existe en instrumentos"))
                continue
            try:
                rows = annual_rows(t, client.company_facts(t), years=o["years"])
                if o["dry_run"]:
                    self.stdout.write(f"{t}: {len(rows)} años (simulación, no se guardó)")
                    continue
                res = import_financials(rows, source="SEC_EDGAR")
            except SecNotSupported as exc:
                skipped.append((t, str(exc)))
                continue
            except SecError as exc:
                failed.append((t, str(exc)))
                continue
            except FundamentalsError as exc:
                failed.append((t, f"{exc} {exc.errors[:2]}"))
                continue
            for k in totals:
                totals[k] += res[k]
            self.stdout.write(
                f"{t}: {res['created']} nuevos, {res['new_versions']} versiones, {res['unchanged']} sin cambios"
            )
        self.stdout.write(f"Total: {totals}")
        for t, why in skipped:
            self.stdout.write(f"Omitida {t}: {why}")
        for t, why in failed:
            self.stderr.write(f"FALLÓ {t}: {why}")
        if failed:
            raise CommandError(f"{len(failed)} emisoras fallaron; las demás se guardaron.")
