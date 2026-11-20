"""Búsqueda externa y seguimiento, sin acceso a cuentas ni operaciones."""

import hashlib
import os
import re
from urllib.parse import urlencode

from django.core import signing
from django.core.cache import cache
from django.db import IntegrityError, OperationalError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.instruments.models import Instrument, InstrumentIdentifier
from modules.pricing.history import refresh_history
from modules.pricing.providers import ProviderError, YahooProvider, http_get_json, reserve_provider

from .models import MarketIdentity, TechnicalClose, WatchlistEntry

SYMBOL = re.compile(r"^[A-Za-z0-9.^=\-]{1,30}$")
MARKETS = {"XNAS": "NMS", "NASDAQ": "NMS", "XNYS": "NYQ", "NYSE": "NYQ", "ARCX": "PCX"}


def _normal_market(value):
    value = str(value or "").upper().strip()
    return MARKETS.get(value, value)


def _search_provider(query):
    key = os.getenv("TWELVE_DATA_API_KEY", "").strip()
    failures = []
    if key:
        try:
            reserve_provider("TWELVE_DATA")
            data = http_get_json(
                "https://api.twelvedata.com/symbol_search?" + urlencode({"symbol": query, "apikey": key})
            )
            if data.get("status") == "error" or not isinstance(data.get("data"), list):
                raise ProviderError("Twelve Data no disponible o límite alcanzado.")
            rows = []
            for item in data["data"]:
                if item.get("instrument_type") not in ("Common Stock", "ETF", "American Depositary Receipt"):
                    continue
                rows.append(
                    {
                        "symbol": item.get("symbol"),
                        "name": item.get("instrument_name"),
                        "market": item.get("mic_code") or item.get("exchange"),
                        "currency": item.get("currency"),
                        "kind": "ETF" if item.get("instrument_type") == "ETF" else "STOCK",
                        "source": "TWELVE_DATA",
                    }
                )
            if rows:
                return rows, failures
        except ProviderError:
            failures.append("Twelve Data no disponible o límite alcanzado; se intentó Yahoo.")
    try:
        reserve_provider("YAHOO_UNOFFICIAL")
        data = http_get_json(
            "https://query2.finance.yahoo.com/v1/finance/search?"
            + urlencode({"q": query, "quotesCount": 20, "newsCount": 0}),
            headers={"User-Agent": YahooProvider.USER_AGENT},
        )
        if not isinstance(data.get("quotes"), list):
            raise ProviderError("Respuesta inesperada.")
        rows = [
            {
                "symbol": x.get("symbol"),
                "name": x.get("shortname") or x.get("longname"),
                "market": x.get("exchange"),
                "currency": x.get("currency"),
                "kind": "ETF" if x.get("quoteType") == "ETF" else "STOCK",
                "source": "YAHOO_UNOFFICIAL",
            }
            for x in data["quotes"]
            if x.get("quoteType") in ("EQUITY", "ETF")
        ]
        return rows, failures
    except ProviderError:
        failures.append("Yahoo no disponible o límite alcanzado.")
        return [], failures


class MarketSearchView(APIView):
    def get(self, request):
        query = request.query_params.get("q", "").strip()
        if not 2 <= len(query) <= 60:
            raise serializers.ValidationError({"q": "Escribe entre 2 y 60 caracteres."})
        key = "technical-market:" + hashlib.sha256(query.casefold().encode()).hexdigest()
        cached = cache.get(key)
        if cached is None:
            rows, warnings = _search_provider(query)
            if rows and not warnings:
                cache.set(key, (rows, warnings), 300)
        else:
            rows, warnings = cached
        result = []
        for row in rows[:20]:
            symbol, market = str(row.get("symbol") or ""), str(row.get("market") or "")
            if not SYMBOL.fullmatch(symbol) or not market or len(market) > 30:
                continue
            clean = {
                "symbol": symbol,
                "market": market,
                "name": str(row.get("name") or symbol)[:200],
                "currency": str(row.get("currency") or "").upper()[:3],
                "kind": row["kind"],
                "source": row["source"],
            }
            clean["token"] = signing.dumps({**clean, "user": request.user.pk}, salt="technical-market")
            result.append(clean)
        return Response({"results": result, "warnings": warnings})


class WatchlistView(APIView):
    def get(self, request):
        entries = (
            WatchlistEntry.objects.filter(user=request.user)
            .select_related("instrument")
            .prefetch_related("instrument__identifiers")
        )
        rows = []
        for entry in entries:
            ident = next(
                (
                    i
                    for i in sorted(entry.instrument.identifiers.all(), key=lambda x: x.id)
                    if i.kind == "TICKER"
                ),
                None,
            )
            if ident:
                rows.append(
                    {
                        "instrument_id": entry.instrument_id,
                        "ticker": ident.value,
                        "name": entry.instrument.name,
                        "market": entry.instrument.market,
                        "currency": entry.instrument.currency,
                    }
                )
        return Response(rows)

    def post(self, request):
        pk = str(request.data.get("instrument_id", ""))
        if not pk.isdigit():
            raise serializers.ValidationError({"instrument_id": "Indica un instrumento válido."})
        instrument = get_object_or_404(Instrument, pk=pk)
        WatchlistEntry.objects.get_or_create(user=request.user, instrument=instrument)
        return Response({"instrument_id": instrument.pk}, status=status.HTTP_201_CREATED)


class WatchlistDetailView(APIView):
    def delete(self, request, pk):
        get_object_or_404(WatchlistEntry, user=request.user, instrument_id=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LoadMarketHistoryView(APIView):
    def post(self, request):
        try:
            return self._load(request)
        except (IntegrityError, OperationalError):
            return Response(
                {"detail": "No se pudo completar la carga concurrente; intenta de nuevo."},
                status=status.HTTP_409_CONFLICT,
            )

    def _load(self, request):
        try:
            item = signing.loads(request.data.get("token", ""), salt="technical-market", max_age=1800)
            if item.get("user") != request.user.pk:
                raise signing.BadSignature()
        except (signing.BadSignature, TypeError, ValueError):
            raise serializers.ValidationError(
                {"token": "Resultado vencido o inválido; busca de nuevo."}
            ) from None
        symbol, market = item["symbol"], item["market"]
        provider = YahooProvider()
        try:
            meta = provider._meta(symbol)
            actual_market = str(meta.get("exchangeName") or "")
            currency = str(meta.get("currency") or "").upper()
            if _normal_market(market) != _normal_market(actual_market) or not re.fullmatch(
                r"[A-Z]{3}", currency
            ):
                raise ProviderError("No se pudo confirmar el mercado o la moneda con Yahoo.")
            if item["currency"] and item["currency"] != currency:
                raise ProviderError("La moneda no coincide con la búsqueda.")
            data = provider.fetch_history(symbol, "5y")
            if data["currency"] != currency or not data["bars"]:
                raise ProviderError("No hay historial compatible para esta emisora.")
        except ProviderError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from None
        # Todas las consultas externas terminan antes de abrir la transacción.
        with transaction.atomic():
            normalized_market = _normal_market(market)
            bound = (
                MarketIdentity.objects.select_related("instrument")
                .filter(symbol=symbol.upper(), market=normalized_market, currency=currency)
                .first()
            )
            candidates = list(
                InstrumentIdentifier.objects.filter(
                    kind="TICKER",
                    value__iexact=symbol,
                    instrument__currency=currency,
                ).select_related("instrument")
            )
            candidates = list({ident.instrument_id: ident for ident in candidates}.values())
            exact = [
                ident
                for ident in candidates
                if _normal_market(ident.instrument.market) == _normal_market(market)
            ]
            if bound:
                exact = [ident for ident in candidates if ident.instrument_id == bound.instrument_id]
            if len(exact) > 1:
                raise serializers.ValidationError(
                    {"detail": "Hay instrumentos duplicados para este símbolo y mercado."}
                )
            requested = request.data.get("instrument_id")
            if requested is not None:
                ident = next((i for i in candidates if str(i.instrument_id) == str(requested)), None)
                if not ident:
                    raise serializers.ValidationError(
                        {"instrument_id": "La selección ya no corresponde a este símbolo y moneda."}
                    )
            elif exact:
                ident = exact[0]
            elif candidates and request.data.get("create_new") is not True:
                return Response(
                    {
                        "requires_selection": True,
                        "symbol": symbol,
                        "market": market,
                        "candidates": [
                            {
                                "instrument_id": i.instrument_id,
                                "name": i.instrument.name,
                                "market": i.instrument.market,
                                "currency": i.instrument.currency,
                            }
                            for i in candidates
                        ],
                    }
                )
            else:
                ident = None
            instrument = (
                ident.instrument
                if ident
                else Instrument.objects.create(
                    name=item["name"],
                    market=market,
                    currency=currency,
                    asset_class=item["kind"],
                    research_only=True,
                )
            )
            if not ident:
                InstrumentIdentifier.objects.create(instrument=instrument, kind="TICKER", value=symbol)
            binding, _ = MarketIdentity.objects.get_or_create(
                symbol=symbol.upper(),
                market=normalized_market,
                currency=currency,
                defaults={"instrument": instrument},
            )
            if binding.instrument_id != instrument.id:
                raise serializers.ValidationError(
                    {"detail": "La identidad del mercado ya fue asignada a otra emisora; vuelve a buscar."}
                )

            class LoadedProvider:
                name = provider.name

                def fetch_history(self, *_args):
                    return data

            result = refresh_history(LoadedProvider(), "5y", {symbol: instrument}, close_model=TechnicalClose)
            if result["failed"]:
                raise serializers.ValidationError({"detail": "No se pudo guardar el historial."})
        return Response(
            {
                "instrument_id": instrument.pk,
                "ticker": symbol,
                "provider": provider.name,
                "last_bar": data["bars"][-1][0],
                "saved": result["saved"].get(symbol, 0),
            }
        )
