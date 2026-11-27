"""Prepara una base temporal con datos ficticios y levanta el backend en el puerto 8010 (lo usa playwright.config)."""

import os
import subprocess
import sys
import tempfile

import openpyxl

db = sys.argv[1]
backend = os.environ["E2E_BACKEND_DIR"]
fixture = openpyxl.Workbook()
sheet = fixture.active
sheet.append(["Mercado de Capitales USA"])
sheet.append(["Emisora/Fondo", "Títulos", "Costo promedio", "Precio mercado", "PPP", "Valor mercado"])
sheet.append(["AAA", 1, "$100.00", "$110.00", "-", "$110.00"])
fixture.save(os.path.join(tempfile.gettempdir(), "asi-e2e-gbm_1794613125783.xlsx"))
for suffix in ("", "-journal"):
    try:
        os.remove(db + suffix)
    except FileNotFoundError:
        pass
py = sys.executable
for cmd in (["migrate", "--noinput"], ["seed_demo"]):
    subprocess.run([py, "manage.py", *cmd], cwd=backend, check=True, stdout=subprocess.DEVNULL)
SEED_BARS = """
import math
from datetime import date, timedelta
from decimal import Decimal as D
from modules.instruments.models import InstrumentIdentifier
from modules.pricing.history_models import DailyClose
ins = InstrumentIdentifier.objects.get(kind="TICKER", value="AAA").instrument
hoy = date.today()
rows = []
for i in range(130):
    c = 100 + math.sin(i / 7) * 6 + i * 0.1
    rows.append(DailyClose(instrument=ins, date=hoy - timedelta(days=130 - i), close=D(str(round(c, 4))), open=D(str(round(c - 0.3, 4))),
                           high=D(str(round(c + 1, 4))), low=D(str(round(c - 1, 4))), volume=1000 + i, currency="USD", provider="E2E"))
DailyClose.objects.bulk_create(rows)
"""
SEED_SEC = """
from modules.fundamentals.services import import_financials
base = dict(ticker="AAA", currency="USD")
rows = [
    dict(base, fiscal_year=2023, period_end="2023-12-31", published_at="2024-02-20", revenue="1000", gross_profit="500", net_income="100", cfo="120", total_assets="2000", current_assets="800", current_liabilities="400", long_term_debt="300", shares_outstanding="100"),
    dict(base, fiscal_year=2024, period_end="2024-12-31", published_at="2025-02-20", revenue="1200", gross_profit="640", net_income="150", cfo="210", capex="70", total_assets="2400", current_assets="900", current_liabilities="400", long_term_debt="250", shares_outstanding="98"),
]
import_financials(rows, source="SEC_EDGAR")
"""
subprocess.run([py, "manage.py", "shell", "-c", SEED_SEC], cwd=backend, check=True, stdout=subprocess.DEVNULL)  # estados con origen SEC (ficticios)
subprocess.run([py, "manage.py", "shell", "-c", SEED_BARS], cwd=backend, check=True, stdout=subprocess.DEVNULL)  # historial ficticio de AAA
sys.exit(subprocess.call([py, "manage.py", "runserver", os.environ.get("E2E_BACKEND_PORT", "8010"), "--noreload"], cwd=backend))  # sin execv: en Windows dejaría un proceso huérfano
