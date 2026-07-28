from pathlib import Path

import environ
from celery.schedules import crontab


BASE_DIR = Path(__file__).resolve().parents[2]
env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="dev-secret-key")
STATE = env("STATE", default="DEV")
DEBUG = env.bool("DEBUG", default=False)

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:4200", "http://127.0.0.1:4200"],
)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# URL publique de la console Angular (lot L5) — sert aux redirections et aux liens.
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL", default="https://billing.foxugly.com")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "django_extensions",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "djstripe",
    "accounts.apps.AccountsConfig",
    "core.apps.CoreConfig",
    "health.apps.HealthConfig",
]

MIDDLEWARE = [
    # Alias transitoire /api/ -> /api/v1/ (config/legacy_api_prefix.py). En tete
    # pour que tout ce qui suit ne voie que le chemin canonique. A retirer quand
    # les logs ne montrent plus de "legacy_api_prefix".
    "config.legacy_api_prefix.LegacyApiPrefixMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

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

# --- Base de données : convention flotte DB_* 6 variables (OPERATIONS.md §3.13) ---
_DB_ENGINE_ALIASES = {
    "sqlite3": "django.db.backends.sqlite3",
    "postgresql": "django.db.backends.postgresql",
    "postgres": "django.db.backends.postgresql",
}
_db_engine = env("DB_ENGINE", default="sqlite3")
DATABASE_NAME = env("DB_NAME", default="").strip() or str(BASE_DIR / "db.sqlite3")

DATABASES = {
    "default": {
        "ENGINE": _DB_ENGINE_ALIASES.get(_db_engine, _db_engine),
        "NAME": DATABASE_NAME,
        "USER": env("DB_USER", default=""),
        "PASSWORD": env("DB_PASSWORD", default=""),
        "HOST": env("DB_HOST", default=""),
        "PORT": env("DB_PORT", default=""),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr"
TIME_ZONE = "Europe/Brussels"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Utilisateur email-only (OPERATIONS.md §3.16). Posé AVANT le premier migrate :
# changer AUTH_USER_MODEL sur une base existante impose un runbook SQL manuel.
AUTH_USER_MODEL = "accounts.User"

# --- Stripe (dj-stripe) ---------------------------------------------------------
# Les clés sont absentes en dev/test et le service démarre sans : rien ici n'appelle
# Stripe. Elles seront seedées en SSM au lot L6, à la mise en service réelle.
STRIPE_TEST_SECRET_KEY = env("STRIPE_TEST_SECRET_KEY", default="")
STRIPE_LIVE_SECRET_KEY = env("STRIPE_LIVE_SECRET_KEY", default="")
# env.bool et NON env : la chaîne "False" est vraie en Python, et dj-stripe
# basculerait en mode live sans le moindre avertissement.
STRIPE_LIVE_MODE = env.bool("STRIPE_LIVE_MODE", default=False)
# "id" est la valeur recommandée pour une installation neuve ; "djstripe_id" n'existe
# que pour les installations antérieures à dj-stripe 2.4. Il n'y a pas de chemin de
# migration entre les deux : le choix se fait maintenant ou jamais.
DJSTRIPE_FOREIGN_KEY_TO_FIELD = "id"

# Jours de grâce accordés après un échec de paiement avant de fermer l'accès (§6.6).
# 0 désactive la grâce. Réglable en SSM sans redéploiement.
BILLING_GRACE_DAYS = env.int("BILLING_GRACE_DAYS", default=7)

# --- Redis : broker Celery (db4) et cache Django (db5) ---------------------------
# db0 à db3 sont déjà pris sur la box (dont db3 par les Channels de Poker).
REDIS_URL = env("REDIS_URL", default="")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL or "memory://")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="")
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=not REDIS_URL)
CELERY_TIMEZONE = TIME_ZONE

CELERY_BEAT_SCHEDULE = {
    # Reprend les livraisons dont l'échéance est passée. Sans ce balayage, une
    # reprise reposerait sur un `countdown` Celery, perdu au redémarrage du worker.
    "flush-pending-deliveries": {
        "task": "core.flush_pending_deliveries",
        "schedule": 300.0,
    },
    # Filet de sécurité : recalcule tout et repousse les écarts (§6.4).
    "reconcile-entitlements": {
        "task": "core.reconcile_entitlements",
        "schedule": crontab(hour=4, minute=30),
    },
}

# L'anti-rejeu des signatures HMAC s'appuie sur ce cache : il DOIT être partagé
# entre les workers gunicorn. Avec LocMem (par processus), un rejeu passerait une
# fois par worker — la protection serait cosmétique. En dev/test, sans Redis, on
# retombe sur LocMem : mono-processus, donc correct.
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL.rsplit("/", 1)[0] + "/5",
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Foxugly Billing API",
    "DESCRIPTION": "Service de facturation centralisé de la flotte Foxugly.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

# --- Sentry : actif uniquement sous STATE=PROD (§3.8). Le DSN est un String en
# SSM, pas un SecureString : il est de toute façon embarqué dans les bundles SPA.
SENTRY_DSN = env("SENTRY_DSN", default="")

if STATE == "PROD" and SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=env("SENTRY_ENVIRONMENT", default="production"),
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0),
        send_default_pii=False,
    )

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "{levelname} {asctime} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "loggers": {"billing": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}
