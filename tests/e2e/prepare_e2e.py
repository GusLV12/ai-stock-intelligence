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
sys.exit(subprocess.call([py, "manage.py", "runserver", "8010", "--noreload"], cwd=backend))  # sin execv: en Windows dejaría un proceso huérfano
