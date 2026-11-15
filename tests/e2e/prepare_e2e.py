"""Prepara una base temporal con datos ficticios y levanta el backend en el puerto 8010 (lo usa playwright.config)."""

import os
import subprocess
import sys

db = sys.argv[1]
backend = os.environ["E2E_BACKEND_DIR"]
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
subprocess.run([py, "manage.py", "shell", "-c", SEED_BARS], cwd=backend, check=True, stdout=subprocess.DEVNULL)  # historial ficticio de AAA
sys.exit(subprocess.call([py, "manage.py", "runserver", "8010", "--noreload"], cwd=backend))  # sin execv: en Windows dejaría un proceso huérfano
