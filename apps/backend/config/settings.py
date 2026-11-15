"""Configuración de Django para AI Stock Intelligence.

Toda la configuración sensible viene de variables de entorno (ver .env.example).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR.parent.parent / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("DJANGO_SECRET_KEY es obligatorio cuando DJANGO_DEBUG=false")
    SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"

ALLOWED_HOSTS = [h for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "modules.core",
    "modules.accounts",
    "modules.instruments",
    "modules.portfolio",
    "modules.imports",
    "modules.pricing",
    "modules.mcp_access",
    "modules.assistant",
    "modules.valuations",
    "modules.calculators",
    "modules.fundamentals",
    "modules.intelligence",
    "modules.technical",
    "modules.quant",
    "modules.paper",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "modules.core.observability.RequestIdMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# PostgreSQL es la base objetivo. SQLite solo se usa si no hay DB_HOST (pruebas rápidas).
if os.getenv("DB_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "asi"),
            "USER": os.getenv("DB_USER", "asi"),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.environ["DB_HOST"],
            "PORT": os.getenv("DB_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("SQLITE_PATH") or str(BASE_DIR / "db.sqlite3"),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-mx"
TIME_ZONE = "America/Mexico_City"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Seguridad por defecto: todo requiere autenticación salvo que la vista lo declare.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "login": "5/min",
        "assistant": "10/min",
        "intraday": "6/min",
        "quant": "6/min",
    },
    # Las vistas de exportación interpretan ?format= (csv/json) ellas mismas; DRF lo usaría para elegir renderizador.
    "URL_FORMAT_OVERRIDE": None,
}

# Sesión y cookies: HttpOnly para la sesión; en producción exigir HTTPS.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_TRUSTED_ORIGINS = [
    o for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:3000").split(",") if o
]

# --- Endurecimiento para el despliegue remoto (plan 17/20). Con DJANGO_DEBUG=false todo esto queda activo. ---
BEHIND_PROXY = env_bool("DJANGO_BEHIND_PROXY", False)  # TLS termina en el proxy inverso (Caddy/nginx)
if BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SESSION_COOKIE_AGE = int(os.getenv("DJANGO_SESSION_HOURS", "12")) * 3600
DATA_UPLOAD_MAX_MEMORY_SIZE = (
    15 * 1024 * 1024
)  # los XLSX/CSV de importación son pequeños; nada de cargas enormes
FILE_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", str(60 * 60 * 24 * 365)))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_SUBDOMAINS", True)
    SECURE_HSTS_PRELOAD = env_bool(
        "DJANGO_HSTS_PRELOAD", False
    )  # solo cuando el dominio esté listo para la lista de precarga
    CSRF_COOKIE_HTTPONLY = True  # el cliente obtiene el token por /api/v1/auth/csrf
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
        raise RuntimeError("DJANGO_ALLOWED_HOSTS debe listar dominios concretos cuando DJANGO_DEBUG=false")
    if any(
        o.startswith("http://") and "localhost" not in o and "127.0.0.1" not in o
        for o in CSRF_TRUSTED_ORIGINS
    ):
        raise RuntimeError("DJANGO_CSRF_TRUSTED_ORIGINS debe usar https:// en producción")

# W021 (precarga HSTS) se silencia a propósito: la precarga es casi irreversible y solo debe activarla el dueño del dominio
# con DJANGO_HSTS_PRELOAD=true; mientras tanto HSTS de un año ya protege a los navegadores que visitaron el sitio.
if not DEBUG and not SECURE_HSTS_PRELOAD:
    SILENCED_SYSTEM_CHECKS = ["security.W021"]

# El panel /admin no se expone en producción salvo que se defina una ruta propia y no obvia.
ADMIN_URL = os.getenv("DJANGO_ADMIN_URL", "admin/" if DEBUG else "").strip("/")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact": {"()": "modules.core.observability.RedactFilter"}},
    "formatters": {"json": {"()": "modules.core.observability.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json", "filters": ["redact"]}},
    "root": {"handlers": ["console"], "level": os.getenv("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {"django.server": {"level": "INFO"}},
}
