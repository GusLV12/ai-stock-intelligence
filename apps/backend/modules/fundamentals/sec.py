"""Ingesta de estados financieros ANUALES desde SEC EDGAR (datos públicos y gratuitos de la Comisión de Valores de EE. UU.).

Reglas y límites (declarados, no escondidos):
* La SEC exige identificarse en el encabezado User-Agent con un contacto. Se lee de la variable de entorno `SEC_CONTACT`
  (p. ej. "Nombre Apellido correo@dominio"); NO tiene valor por defecto ni se guarda en el repositorio. Sin ella no se hace
  ninguna consulta. Ese dato se envía a los servidores de la SEC en cada petición.
* Cortesía de tasa: la SEC permite hasta 10 consultas por segundo; aquí se espera ≥ 0.15 s entre consultas.
* Solo informes anuales 10-K (norma contable US-GAAP). Las emisoras extranjeras que presentan 20-F/40-F (por ejemplo TSM, SONY)
  y los fondos (ETF, metales) NO están soportados: se reportan como tales, no se inventan datos; usa la importación por CSV.
* Se usa el valor tal como se publicó ORIGINALMENTE (el informe más antiguo del periodo) con su fecha de presentación como
  `published_at`: así no hay sesgo de anticipación. Las reexpresiones posteriores NO se capturan automáticamente.
* EBITDA y deuda total son DERIVADOS (utilidad operativa + depreciación y amortización; suma de componentes de deuda). Si falta
  un componente necesario, queda vacío. La deuda total excluye arrendamientos.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from decimal import Decimal

ALLOWED_HOSTS = ("www.sec.gov", "data.sec.gov")
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
MAX_BYTES = 60 * 1024 * 1024  # companyfacts de una emisora grande pesa decenas de MB
PAUSE_SECONDS = 0.15

# Conceptos US-GAAP por campo, en orden de preferencia (el primero que tenga dato anual gana).
FLOW = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "depreciation": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
    ],
}
INSTANT = {
    "total_assets": ["Assets"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "long_term_debt": ["LongTermDebtNoncurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
}
DEBT_COMPONENTS = ["LongTermDebtNoncurrent", "LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper"]
SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding"]


class SecError(Exception):
    """Fallo de la ingesta. El mensaje nunca incluye el contacto ni URLs con datos personales."""


class SecNotSupported(SecError):
    """La emisora existe pero este método no puede darle estados financieros (ETF, 20-F, sin 10-K...)."""


def contact_from_env() -> str:
    contact = (os.getenv("SEC_CONTACT") or "").strip()
    if not contact or "@" not in contact or len(contact) > 200:
        raise SecError(
            "Falta SEC_CONTACT: la SEC exige identificar al solicitante (nombre y correo). Defínelo en tu .env local "
            '(p. ej. SEC_CONTACT="Nombre Apellido correo@dominio"); nunca se sube al repositorio.'
        )
    return contact


def http_get(url: str, headers: dict, timeout: int = 30) -> dict:
    host = url.split("/")[2] if url.startswith("https://") else ""
    if host not in ALLOWED_HOSTS:
        raise SecError("Solo se permiten conexiones HTTPS a los servidores de la SEC.")
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - host y esquema validados arriba
            raw = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SecNotSupported("La SEC no tiene datos para esta emisora.") from None
        raise SecError(f"La SEC respondió HTTP {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SecError("No se pudo conectar con la SEC.") from None
    if len(raw) > MAX_BYTES:
        raise SecError("Respuesta de la SEC demasiado grande.")
    try:
        data = json.loads(raw)
    except ValueError:
        raise SecError("La SEC no devolvió JSON válido.") from None
    if not isinstance(data, dict):
        raise SecError("Respuesta inesperada de la SEC.")
    return data


class SecClient:
    def __init__(self, contact: str | None = None, http=http_get, sleep=time.sleep, clock=time.monotonic):
        self._contact = contact if contact is not None else contact_from_env()
        self._http, self._sleep, self._clock, self._last = http, sleep, clock, None
        self._tickers: dict[str, int] | None = None

    def _get(self, url: str) -> dict:
        if self._last is not None and (wait := PAUSE_SECONDS - (self._clock() - self._last)) > 0:
            self._sleep(wait)
        self._last = self._clock()
        return self._http(
            url, {"User-Agent": f"ai-stock-intelligence {self._contact}", "Accept": "application/json"}
        )

    def cik(self, ticker: str) -> int:
        if self._tickers is None:
            data = self._get(TICKERS_URL)
            self._tickers = {}
            for row in data.values():
                if isinstance(row, dict) and "ticker" in row and "cik_str" in row:
                    self._tickers[str(row["ticker"]).upper()] = int(row["cik_str"])
        key = ticker.upper()
        for candidate in (key, key.replace(".", "-"), key.replace("-", ".")):
            if candidate in self._tickers:
                return self._tickers[candidate]
        raise SecNotSupported("No está en el listado de emisoras de la SEC (¿fondo, ETF o metal?).")

    def company_facts(self, ticker: str) -> dict:
        return self._get(FACTS_URL.format(cik=self.cik(ticker)))


# ------------------------------------------------------------------------------ mapeo (puro)
def _fiscal_year(end: date) -> int:
    """Año fiscal = año del cierre del periodo (los cierres de 1 a 7 de enero de años de 52/53 semanas cuentan al anterior)."""
    return end.year - 1 if end.month == 1 and end.day <= 7 else end.year


def _annual_entries(
    facts: dict, concept: str, units: tuple[str, ...], flow: bool
) -> dict[date, tuple[date, Decimal]]:
    """{cierre: (presentado, valor)} con el valor ORIGINAL (informe 10-K presentado primero) de cada año."""
    out: dict[date, tuple[date, Decimal]] = {}
    node = (facts.get("us-gaap") or {}).get(concept) or {}
    for unit in units:
        for e in (node.get("units") or {}).get(unit, []):
            form = str(e.get("form", ""))
            if not form.startswith("10-K") or e.get("val") is None:
                continue
            try:
                end, filed = date.fromisoformat(e["end"]), date.fromisoformat(e["filed"])
                if flow and not timedelta(days=350) <= end - date.fromisoformat(e["start"]) <= timedelta(
                    days=380
                ):
                    continue  # trimestres y acumulados no son un año completo
                val = Decimal(str(e["val"]))
            except (KeyError, ValueError, ArithmeticError):
                continue
            if end not in out or filed < out[end][0]:
                out[end] = (filed, val)
    return out


def _pick(facts: dict, concepts: list[str], units: tuple[str, ...], flow: bool):
    for concept in concepts:
        found = _annual_entries(facts, concept, units, flow)
        if found:
            return found
    return {}


def annual_rows(ticker: str, payload: dict, years: int = 10) -> list[dict]:
    """Convierte el JSON de companyfacts en filas para `import_financials`. Lanza SecNotSupported si no hay 10-K en US-GAAP."""
    facts = payload.get("facts")
    if not isinstance(facts, dict) or "us-gaap" not in facts:
        raise SecNotSupported(
            "Sin datos US-GAAP (probablemente emisora extranjera con 20-F/IFRS): usa la importación por CSV."
        )
    usd, shares_units = ("USD",), ("shares",)
    series = {f: _pick(facts, c, usd, True) for f, c in FLOW.items()}
    series.update({f: _pick(facts, c, usd, False) for f, c in INSTANT.items()})
    series["shares_outstanding"] = _pick(facts, SHARES, shares_units, True)
    debt = {c: _annual_entries(facts, c, usd, False) for c in DEBT_COMPONENTS}
    # Los años de referencia salen de utilidad neta o ingresos: sin ninguno no hay un año fiscal reconocible.
    anchors = set(series["net_income"]) | set(series["revenue"])
    if not anchors:
        raise SecNotSupported("Sin informes anuales 10-K con resultados: usa la importación por CSV.")
    rows = []
    for end in sorted(anchors, reverse=True)[:years]:
        filed_dates, row = (
            [],
            {
                "ticker": ticker.upper(),
                "fiscal_year": _fiscal_year(end),
                "period_end": end.isoformat(),
                "currency": "USD",
            },
        )
        for field, s in series.items():
            if field == "depreciation" or end not in s:
                continue
            filed, val = s[end]
            row[field] = str(abs(val) if field == "capex" else val)
            filed_dates.append(filed)
        # Derivados (declarados en el docstring del módulo).
        if end in series["operating_income"] and end in series["depreciation"]:
            row["ebitda"] = str(series["operating_income"][end][1] + series["depreciation"][end][1])
            filed_dates.append(max(series["operating_income"][end][0], series["depreciation"][end][0]))
        parts = [debt[c][end] for c in DEBT_COMPONENTS if end in debt[c]]
        if parts and end in debt["LongTermDebtNoncurrent"]:
            row["total_debt"] = str(sum((v for _, v in parts), Decimal(0)))
            filed_dates.extend(f for f, _ in parts)
        row["published_at"] = max(filed_dates).isoformat()
        rows.append(row)
    return rows
