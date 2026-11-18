"""Fail-closed settings for the invitation-only portal.

No research database or market provider credentials are read by this app.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv("DEBUG", "0") == "1"
SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY or (not DEBUG and len(SECRET_KEY) < 32):
    raise ImproperlyConfigured("Set a private SECRET_KEY of at least 32 characters in production")

ALLOWED_HOSTS = [s.strip() for s in os.getenv("ALLOWED_HOSTS", "").split(",") if s.strip()]
if not DEBUG and (not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS):
    raise ImproperlyConfigured("Production ALLOWED_HOSTS must name explicit hosts")
CSRF_TRUSTED_ORIGINS = [s.strip() for s in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if s.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    *([] if DEBUG else ["whitenoise.middleware.WhiteNoiseMiddleware"]),
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.UserTimezoneMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "portal.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug",
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "core.context_processors.deployment",
    ]},
}]
WSGI_APPLICATION = "portal.wsgi.application"

if os.getenv("POSTGRES_HOST"):
    required = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ImproperlyConfigured("Missing PostgreSQL settings: " + ", ".join(missing))
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ["POSTGRES_HOST"],
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }}
elif DEBUG or os.getenv("DJANGO_TESTING") == "1":
    (BASE_DIR / "var").mkdir(parents=True, exist_ok=True)
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "var" / "dev.sqlite3",
    }}
else:
    raise ImproperlyConfigured("Production requires PostgreSQL settings")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {"staticfiles": {"BACKEND": (
    "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG
    else "whitenoise.storage.CompressedManifestStaticFilesStorage"
)}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_AGE = 12 * 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
TRUSTED_REVERSE_PROXY = os.getenv("TRUSTED_REVERSE_PROXY", "0") == "1"
PUBLIC_MARKET_DATA_ENABLED = os.getenv("PUBLIC_MARKET_DATA_ENABLED", "0") == "1"
PERSONAL_MODE = os.getenv("PERSONAL_MODE", "0") == "1"
if PERSONAL_MODE and not DEBUG and not TRUSTED_REVERSE_PROXY:
    raise ImproperlyConfigured("Production personal mode requires a trusted HTTPS reverse proxy")
if PERSONAL_MODE and PUBLIC_MARKET_DATA_ENABLED:
    raise ImproperlyConfigured("Personal mode cannot enable public market-data display")
PERSONAL_FEED_OWNER_USERNAME = "owner@local.invalid"
PERSONAL_FEED_DB_PATH = BASE_DIR / "var" / "personal_feeds.sqlite3"
OFFICIAL_FILINGS_DB_PATH = BASE_DIR / "var" / "official_filings.sqlite3"
# Where this portal runs, shown on the page. "local" keeps the original
# Windows label; the cloud deployment sets DEPLOYMENT_KIND=cloud_private.
DEPLOYMENT_KIND = os.getenv("DEPLOYMENT_KIND", "local")
if DEPLOYMENT_KIND not in {"local", "cloud_private"}:
    raise ImproperlyConfigured("DEPLOYMENT_KIND must be local or cloud_private")
# A provider whose collection is deliberately not started on this deployment
# (an authorization or permission condition is still open). Empty = no gate.
PROVIDER_GATE_CODES = {"PENDING_SUPPORT_REPLY", "PENDING_WRITTEN_PERMISSION"}
PROVIDER_GATES = {name: os.getenv(f"PROVIDER_GATE_{name.upper()}", "") for name in ("moomoo", "jin10")}
if any(code and code not in PROVIDER_GATE_CODES for code in PROVIDER_GATES.values()):
    raise ImproperlyConfigured("Unknown PROVIDER_GATE_* code")
if TRUSTED_REVERSE_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = not DEBUG
    # Container health probes call the app directly on its private port; only
    # these two bodyless status endpoints skip the HTTPS redirect.
    SECURE_REDIRECT_EXEMPT = [r"^healthz/$", r"^readyz/$"]
    SECURE_HSTS_SECONDS = 3600 if not DEBUG else 0
