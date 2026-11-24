"""Actualiza velas técnicas de cartera sin escribir en las series de Quant Lab."""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.portfolio.models import TaxLot
from modules.pricing.history import refresh_history
from modules.pricing.history_models import DailyClose
from modules.pricing.providers import ProviderError, ProviderRateLimit, YahooProvider

from .models import MarketIdentity, PortfolioHistoryRun, TechnicalClose
from .search_api import _normal_market

US_MARKETS = {"NMS", "NGM", "NCM", "NYQ", "PCX", "ASE", "BTS"}


class RefreshCancelled(Exception):
    """La ejecución perdió el permiso para actualizar la corrida."""


def create_run(user, source="manual", schedule_date=None):
    # Solo se recuperan trabajos que no han actualizado su progreso durante 30 minutos.
    PortfolioHistoryRun.objects.filter(user=user, status="running", updated_at__lt=timezone.now() - timedelta(minutes=30)).update(
        status="interrupted", finished_at=timezone.now()
    )
    try:
        with transaction.atomic():
            return PortfolioHistoryRun.objects.create(user=user, source=source, schedule_date=schedule_date), True
    except IntegrityError:
        return PortfolioHistoryRun.objects.filter(user=user, status="running").first(), False


def _portfolio(user):
    ids = TaxLot.objects.filter(account__owner=user, quantity_remaining__gt=0).values_list("instrument_id", flat=True).distinct()
    return list(Instrument.objects.filter(id__in=ids, asset_class__in=["STOCK", "ETF", "ADR"]).order_by("id").prefetch_related("identifiers"))


def _load_one(provider, ident, run_id=None, lease=None):
    symbol, instrument = ident.value.upper(), ident.instrument
    # Un ticker repetido puede referirse a otra bolsa. Exigir vínculo explícito si hay ambigüedad.
    matches = InstrumentIdentifier.objects.filter(kind="TICKER", value__iexact=symbol).values("instrument_id").distinct().count()
    identity = MarketIdentity.objects.filter(instrument=instrument, symbol__iexact=symbol).first()
    if matches > 1 and identity is None:
        raise ProviderError("Símbolo ambiguo: confirma primero su identidad de mercado en el buscador.")
    meta = provider._meta(symbol)
    market = _normal_market(meta.get("exchangeName"))
    expected = _normal_market(identity.market if identity else instrument.market)
    if (str(meta.get("symbol", "")).upper().replace(".", "-") != symbol.replace(".", "-")
            or str(meta.get("currency", "")).upper() != instrument.currency
            or (expected not in {"USA (GBM)", "US"} and market != expected)
            or (expected in {"USA (GBM)", "US"} and market not in US_MARKETS)):
        raise ProviderError("No coincide el símbolo, mercado o moneda del proveedor.")
    data = provider.fetch_history(symbol, "5y")
    if data["currency"] != instrument.currency or not data["bars"]:
        raise ProviderError("Historial vacío o moneda incompatible.")
    legacy = DailyClose.objects.filter(instrument=instrument)
    technical = TechnicalClose.objects.filter(instrument=instrument)
    prior_count = max(legacy.count(), technical.count())
    dates = [x for x in (legacy.order_by("-date").values_list("date", flat=True).first(), technical.order_by("-date").values_list("date", flat=True).first()) if x]
    prior_last = max(dates) if dates else None
    if len(data["bars"]) < min(prior_count, 1000) or (prior_last and data["bars"][-1][0] < prior_last):
        raise ProviderError("Historial insuficiente o anterior al ya guardado; se conserva el existente.")

    class Loaded:
        name = provider.name

        def fetch_history(self, *_args):
            return data

    with transaction.atomic():
        if run_id is not None and not PortfolioHistoryRun.objects.select_for_update().filter(
            pk=run_id, status="running", started_at=lease
        ).exists():
            raise RefreshCancelled()
        result = refresh_history(Loaded(), "5y", {symbol: instrument}, close_model=TechnicalClose)
    if result["failed"]:
        raise ProviderError("No se pudo guardar el historial técnico.")
    return {"ticker": symbol, "status": "updated" if symbol in result["saved"] else "unchanged", "last_bar": data["bars"][-1][0].isoformat(), "provider": provider.name}


def execute_run(run_id, provider=None):
    run = PortfolioHistoryRun.objects.select_related("user").get(pk=run_id)
    if run.status != "running":
        return
    provider = provider or YahooProvider(include_closed_today=True)
    instruments = _portfolio(run.user)
    lease = run.started_at
    active = lambda: PortfolioHistoryRun.objects.filter(pk=run.pk, status="running", started_at=lease)
    if not active().update(total=len(instruments), updated_at=timezone.now()):
        return
    try:
        for instrument in instruments:
            if not active().exists():
                return
            entries = [entry for entry in instrument.identifiers.all() if entry.kind == "TICKER"]
            ticker = entries[0].value if entries else instrument.name
            try:
                if len(entries) != 1:
                    raise ProviderError("El instrumento no tiene un único ticker; requiere revisión.")
                ident = entries[0]
                try:
                    result = _load_one(provider, ident, run.pk, lease)
                except ProviderRateLimit as exc:
                    import time
                    time.sleep(exc.retry_after + 1)
                    result = _load_one(provider, ident, run.pk, lease)
            except RefreshCancelled:
                return
            except ProviderError as exc:
                result = {"ticker": ticker, "status": "error", "message": str(exc)}
            run.results = [*run.results, result]
            if not active().update(results=run.results, updated_at=timezone.now()):
                return
        run.status = "completed"
    except Exception:
        run.status = "failed"
        raise
    finally:
        active().update(status=run.status, finished_at=timezone.now(), updated_at=timezone.now())
