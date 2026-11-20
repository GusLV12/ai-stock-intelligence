"""Universo de investigación: acciones estadounidenses grandes y líquidas, para que el Quant Lab tenga suficientes
emisoras (≥ 20) y años de historial sin depender de lo que hay en tu cartera.

Limitación que NO se puede corregir con datos gratuitos: la lista son empresas grandes de HOY (sesgo de supervivencia:
las que quebraron, se fusionaron o salieron del índice no están). Corregirlo exige la composición histórica del índice
fecha por fecha, que es un dato licenciado. Por eso un resultado positivo solo significa "seguir observando", y la lista
está versionada para saber exactamente con cuál se corrió cada experimento. Se incluyen a propósito varias emisoras que
han rendido mal en estos años (INTC, PFE, BA, T, VZ, MMM, DIS) para no tener solo ganadoras, aunque eso mitiga y no
elimina el sesgo."""

from modules.instruments.models import Instrument, InstrumentIdentifier

UNIVERSE_VERSION = "us-large-caps-v1"
RESEARCH_TICKERS = [
    # tecnología y comunicación
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AVGO", "ORCL", "ADBE", "CRM", "AMD", "INTC", "QCOM", "TXN", "MU",
    "CSCO", "IBM", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "TSM", "ASML", "UBER",
    # financieras
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "BRK-B",
    # salud
    "JNJ", "PFE", "MRK", "LLY", "ABBV", "UNH", "TMO", "ABT", "AMGN",
    # consumo
    "WMT", "COST", "HD", "LOW", "TGT", "NKE", "MCD", "SBUX", "KO", "PEP", "PG", "TSLA",
    # energía, industria y servicios públicos
    "XOM", "CVX", "COP", "CAT", "DE", "BA", "GE", "HON", "UPS", "LMT", "MMM", "NEE", "DUK", "SO",
]  # fmt: skip


def ensure_universe(tickers: list[str] | None = None) -> dict[str, Instrument]:
    """Devuelve {ticker: instrumento}. Reutiliza los instrumentos que ya existen (por ejemplo los de tu cartera) y crea
    los demás marcados `research_only`, para que no aparezcan en el catálogo, el screener ni los precios."""
    wanted = [t.strip().upper() for t in (tickers or RESEARCH_TICKERS) if t.strip()]
    found = {}
    for ident in InstrumentIdentifier.objects.filter(kind="TICKER", value__in=wanted).select_related(
        "instrument"
    ):
        ticker = ident.value.upper()
        if ticker in found and found[ticker].id != ident.instrument_id:
            raise ValueError(
                f"El ticker {ticker} identifica varias emisoras; selecciona un instrumento concreto."
            )
        found[ticker] = ident.instrument
    for t in wanted:
        if t in found:
            continue
        ins = Instrument.objects.create(
            asset_class="STOCK", name=t, market="US", currency="USD", research_only=True
        )
        InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=t)
        found[t] = ins
    return {t: found[t] for t in wanted}
