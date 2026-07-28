# Billing — Lot L1 : scaffolding conforme flotte

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mettre en production un service Django `billing` vide mais entièrement conforme aux
règles de la flotte, joignable sur `https://billing-api.foxugly.com/health/` et redéployé
automatiquement à chaque push sur `main`.

**Architecture:** Projet Django 6 classique (WSGI/gunicorn sur `127.0.0.1:8007`, nginx seul
listener public), settings en package `config/settings/` dispatché par `STATE`, secrets lus
depuis AWS SSM via un oneshot root qui écrit `/run/billing/.env` en tmpfs, déploiement GitHub
Actions OIDC → SSM où root installe les artefacts privilégiés depuis le **blob git** et où
l'application est déployée sous l'utilisateur `django`. Aucune logique Stripe dans ce lot.

**Tech Stack:** Python 3.12, Django 6.0.6, DRF 3.17.1, simplejwt, psycopg 3, gunicorn,
pytest + pytest-django, sentry-sdk, PostgreSQL 16 box-local, nginx, systemd.

**Spec de référence :** `docs/superpowers/specs/2026-07-28-billing-central-design.md` (§4 surtout).

## Global Constraints

- **Python 3.12** partout — c'est la version de la box (`Python 3.12.3`, vérifié le 2026-07-28).
  La CI DOIT être en `3.12` : Poker est en `3.13`, c'est une dérive à ne pas reproduire.
- **Django 6.0.6**, **djangorestframework 3.17.1**, **psycopg[binary]>=3.2,<4** — versions
  alignées sur le reste de la flotte.
- App id **`billing`**, port gunicorn **`127.0.0.1:8007`** (8000-8006 occupés, vérifié),
  arbre serveur `/var/www/django_websites/billing_server`.
- Secrets en SSM sous **`/billing/prod`**, **noms nus** (pas de préfixe applicatif dans le nom),
  secrets en `SecureString`. Sélecteur d'environnement = **`STATE`** (`PROD`/`DEV`), **jamais**
  `DJANGO_ENV` (OPERATIONS.md §3.14 : `DJANGO_ENV` est une dérive de quizonline/ical).
- Base de données via la convention **`DB_*` 6 variables** (§3.13). Sans `DB_ENGINE` → sqlite (dev).
- Permissions : dirs **750**, fichiers **640**, `django:www-data`. `umask 027` en tête de tout
  script de déploiement, `UMask=0027` dans toute unit qui écrit.
- **Rien de ce qui est écrit par `django` ne doit s'exécuter en `root`** (§3.10). Le script
  d'env-fetch est installé `root:root 0755` dans `/usr/local/sbin/`, jamais exécuté depuis l'arbre.
- Branche par défaut **`main`**. **Ne jamais pousser vers GitHub depuis l'EC2.**
- Messages de commit en **anglais**, style conventional commits (`feat(scope): …`), comme le
  reste de la flotte. La documentation et les commentaires métier sont en français.
- Aucune dépendance Stripe dans ce lot : `dj-stripe` arrive au lot L2.
- **Écart assumé avec le spec §4 :** le spec liste quatre units (`billing-env-fetch`,
  `billing-gunicorn`, `billing-celery`, `billing-celery-beat`). Ce lot n'installe que les
  **deux premières**. Celery n'a rien à faire tant que la file de livraison n'existe pas (lot L3) ;
  installer un worker qui ne consomme aucune tâche ajoute une unit à surveiller pour rien. Les
  deux units Celery et Redis db4 arrivent avec le lot L3, qui ajoutera une ligne à la boucle
  `for u in …` du workflow de déploiement.

---

### Task 1 : Squelette Django, settings flotte et endpoint `/health/`

**Files:**
- Create: `requirements.txt`
- Create: `manage.py`
- Create: `config/__init__.py`, `config/settings/__init__.py`, `config/settings/base.py`,
  `config/settings/dev.py`, `config/settings/prod.py`, `config/settings/test.py`
- Create: `config/urls.py`, `config/wsgi.py`
- Create: `health/__init__.py`, `health/apps.py`, `health/urls.py`, `health/views.py`
- Create: `.gitignore`, `pytest.ini`, `README.md`
- Test: `health/tests/__init__.py`, `health/tests/test_health.py`

**Interfaces:**
- Consumes: rien (première tâche).
- Produces: le package `config.settings` (dispatch par `STATE`), l'URL nommée `health` montée
  sur `/health/`, et la fixture pytest standard `client` (fournie par pytest-django).

- [ ] **Step 1 : Créer le venv et les dépendances**

```bash
cd /d/Projects/PycharmProjects/billing_server
py -3.12 -m venv .venv          # Windows ; sur la box : python3.12 -m venv .venv
.venv/Scripts/python -m pip install --upgrade pip
```

`requirements.txt` :

```
Django==6.0.6
djangorestframework==3.17.1
djangorestframework-simplejwt==5.5.1
django-cors-headers==4.9.0
django-environ==0.13.0
psycopg[binary]>=3.2,<4
drf-spectacular==0.29.0
sentry-sdk[django]>=2.20,<3
gunicorn==23.0.0
django-extensions>=4,<5
pytest>=8.3,<9
pytest-django==4.12.0
```

```bash
.venv/Scripts/pip install -r requirements.txt
```

- [ ] **Step 2 : Écrire le test qui échoue**

`health/tests/test_health.py` :

```python
import pytest


@pytest.mark.django_db
def test_health_returns_ok_with_database(client):
    response = client.get("/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_is_not_behind_authentication(client, db):
    """UptimeRobot appelle l'endpoint sans credentials (§3.9)."""
    assert client.get("/health/").status_code == 200
```

`health/tests/__init__.py` : fichier vide.

`pytest.ini` :

```ini
[pytest]
DJANGO_SETTINGS_MODULE = config.settings.test
python_files = tests.py test_*.py *_tests.py
```

- [ ] **Step 3 : Lancer le test pour vérifier qu'il échoue**

Run: `.venv/Scripts/pytest health -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'config'`.

- [ ] **Step 4 : Créer le projet Django**

`manage.py` :

```python
#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
```

`config/__init__.py` : fichier vide.

`config/settings/__init__.py` — dispatch par `STATE` uniquement (pas de `DJANGO_ENV`) :

```python
import os


state = os.environ.get("STATE", "DEV").strip().upper()

if state == "PROD":
    from .prod import *  # noqa: F401,F403
elif state == "TEST":
    from .test import *  # noqa: F401,F403
else:
    from .dev import *  # noqa: F401,F403
```

`config/settings/base.py` :

```python
from pathlib import Path

import environ


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
    "health.apps.HealthConfig",
]

MIDDLEWARE = [
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

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "{levelname} {asctime} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "loggers": {"billing": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}
```

`config/settings/dev.py` :

```python
from .base import *  # noqa: F401,F403

DEBUG = True
```

`config/settings/prod.py` :

```python
from .base import *  # noqa: F401,F403

DEBUG = False

# nginx termine le TLS (OPERATIONS.md §3.6).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)  # noqa: F405
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
```

`config/settings/test.py` :

```python
from .base import *  # noqa: F401,F403

DEBUG = False

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
```

`config/urls.py` :

```python
from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", include("health.urls")),
]
```

`config/wsgi.py` :

```python
import os

from django.core.wsgi import get_wsgi_application


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
```

- [ ] **Step 5 : Implémenter l'app `health`**

`health/__init__.py` : fichier vide.

`health/apps.py` :

```python
from django.apps import AppConfig


class HealthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "health"
```

`health/views.py` :

```python
from django.db import connection
from django.http import JsonResponse


def health(request):
    """Liveness + check DB (OPERATIONS.md §3.9). UptimeRobot asserte le mot-clé."""
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        db_ok = False

    status = "ok" if db_ok else "degraded"
    return JsonResponse(
        {"status": status, "database": "ok" if db_ok else "error"},
        status=200 if db_ok else 503,
    )
```

`health/urls.py` :

```python
from django.urls import path

from .views import health


urlpatterns = [path("", health, name="health")]
```

- [ ] **Step 6 : Lancer les tests pour vérifier qu'ils passent**

Run: `.venv/Scripts/pytest health -q`
Expected: PASS — 2 passed.

- [ ] **Step 7 : Vérifier que Django est cohérent**

Run: `.venv/Scripts/python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 8 : Ajouter `.gitignore` et `README.md`**

`.gitignore` :

```
.venv/
__pycache__/
*.py[cod]
db.sqlite3
staticfiles/
.env
.idea/
.pytest_cache/
```

`README.md` :

```markdown
# billing_server — facturation centralisée de la flotte Foxugly

Service Django qui détient Stripe pour l'ensemble des sites Foxugly : il reçoit les webhooks,
mire les objets Stripe (dj-stripe) et pousse aux applications les droits (entitlements) qui en
découlent. Les sites de la flotte ne parlent jamais à Stripe directement.

- Design : `docs/superpowers/specs/2026-07-28-billing-central-design.md`
- Ops : `OPERATIONS.md` du repo `Foxugly/foxugly-ops` (source de vérité)
- Port gunicorn : `127.0.0.1:8007` · vhosts `billing-api.foxugly.com` / `billing.foxugly.com`

## Développement

    python3.12 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/pytest -q
    .venv/bin/python manage.py runserver
```

- [ ] **Step 9 : Commit**

```bash
git add -A
git commit -m "feat(config): Django project skeleton with fleet settings and /health/"
```

---

### Task 2 : Utilisateur email-only — à poser AVANT toute migration en production

**Pourquoi cette tâche est ici et pas au lot L5 :** la console d'admin (L5) a besoin d'un login
staff, donc d'un `AUTH_USER_MODEL`. Changer `AUTH_USER_MODEL` après le premier `migrate` en
production est un cauchemar documenté — voir OPERATIONS.md §3.16, où foxugly a dû passer par un
runbook SQL manuel exécuté sur une copie de la base de prod. On pose le modèle maintenant,
alors que la base n'existe pas encore.

**Files:**
- Create: `accounts/__init__.py`, `accounts/apps.py`, `accounts/models.py`, `accounts/admin.py`
- Create: `accounts/migrations/__init__.py`
- Modify: `config/settings/base.py` (ajouter l'app + `AUTH_USER_MODEL`)
- Test: `accounts/tests/__init__.py`, `accounts/tests/test_user_model.py`

**Interfaces:**
- Consumes: `config.settings.base.INSTALLED_APPS` (Task 1).
- Produces: `accounts.models.User` (`USERNAME_FIELD = "email"`, pas de champ `username`) et son
  `UserManager` avec `create_user(email, password=None, **extra)` /
  `create_superuser(email, password=None, **extra)`. `AUTH_USER_MODEL = "accounts.User"`.
  Les lots L2 à L7 référencent l'utilisateur via `settings.AUTH_USER_MODEL`.

- [ ] **Step 1 : Écrire les tests qui échouent**

`accounts/tests/test_user_model.py` :

```python
import pytest
from django.contrib.auth import get_user_model


User = get_user_model()


@pytest.mark.django_db
def test_create_user_keys_on_email():
    user = User.objects.create_user(email="ops@foxugly.com", password="s3cret!")

    assert user.email == "ops@foxugly.com"
    assert user.check_password("s3cret!")
    assert user.is_staff is False
    assert user.is_superuser is False


@pytest.mark.django_db
def test_create_superuser_is_staff_and_superuser():
    user = User.objects.create_superuser(email="boss@foxugly.com", password="s3cret!")

    assert user.is_staff is True
    assert user.is_superuser is True


@pytest.mark.django_db
def test_email_is_the_username_field_and_has_no_username_column():
    assert User.USERNAME_FIELD == "email"
    assert User.REQUIRED_FIELDS == []
    assert "username" not in [f.name for f in User._meta.get_fields()]


@pytest.mark.django_db
def test_email_is_unique():
    User.objects.create_user(email="dup@foxugly.com", password="x")

    with pytest.raises(Exception):
        User.objects.create_user(email="dup@foxugly.com", password="x")


@pytest.mark.django_db
def test_email_is_required():
    with pytest.raises(ValueError):
        User.objects.create_user(email="", password="x")
```

`accounts/tests/__init__.py` : fichier vide.

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `.venv/Scripts/pytest accounts -q`
Expected: FAIL — le modèle utilisateur est encore `auth.User`, qui possède un champ `username`.

- [ ] **Step 3 : Implémenter le modèle**

`accounts/__init__.py`, `accounts/migrations/__init__.py` : fichiers vides.

`accounts/apps.py` :

```python
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
```

`accounts/models.py` :

```python
"""Utilisateur email-only, conforme au standard flotte (OPERATIONS.md §3.16).

Le service billing n'a pas d'utilisateurs publics : ces comptes sont ceux des
opérateurs (staff) qui accèdent à la console et au Django admin.
"""
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    """Manager qui clé sur l'email : le manager standard exige un username."""

    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("L'email est obligatoire.")
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Un superuser doit avoir is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Un superuser doit avoir is_superuser=True.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email

    def get_full_name(self):
        full = f"{self.first_name} {self.last_name}".strip()
        return full or self.email

    def get_short_name(self):
        return self.first_name or self.email
```

`accounts/admin.py` — l'admin de Django est câblé sur `username`, il faut le remplacer :

```python
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "is_staff", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("email", "first_name", "last_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Identité", {"fields": ("first_name", "last_name")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),
    )
```

- [ ] **Step 4 : Câbler l'app dans les settings**

Dans `config/settings/base.py`, ajouter `"accounts.apps.AccountsConfig",` à `INSTALLED_APPS`
juste avant `"health.apps.HealthConfig",`, puis ajouter après le bloc `DEFAULT_AUTO_FIELD` :

```python
AUTH_USER_MODEL = "accounts.User"
```

- [ ] **Step 5 : Générer la migration**

Run: `.venv/Scripts/python manage.py makemigrations accounts`
Expected: `accounts/migrations/0001_initial.py` créé, contenant `User` **sans** champ `username`.

- [ ] **Step 6 : Lancer les tests pour vérifier qu'ils passent**

Run: `.venv/Scripts/pytest -q`
Expected: PASS — 7 passed (2 de health + 5 de accounts).

- [ ] **Step 7 : Vérifier qu'aucune migration n'est manquante**

Run: `.venv/Scripts/python manage.py makemigrations --check --dry-run`
Expected: `No changes detected` (code de sortie 0).

- [ ] **Step 8 : Commit**

```bash
git add -A
git commit -m "feat(accounts): email-only staff user model before any production migrate"
```

---

### Task 3 : Intégration continue GitHub Actions et création du dépôt

**Files:**
- Create: `.github/workflows/deploy.yml` (job `test` seulement — le job `deploy` arrive en Task 5)

**Interfaces:**
- Consumes: `requirements.txt`, `pytest.ini` (Task 1) ; la suite de tests (Tasks 1-2).
- Produces: un workflow nommé `Test & Deploy` avec un job `test` que le job `deploy` de la
  Task 5 déclarera en `needs: test`.

- [ ] **Step 1 : Écrire le workflow**

`.github/workflows/deploy.yml` :

```yaml
name: Test & Deploy

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

concurrency:
  # Clé sur la ref : les runs de PR ne font jamais la queue derrière un deploy de main.
  group: deploy-production-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5

      - uses: actions/setup-python@v6
        with:
          # 3.12 = la version de la box (Python 3.12.3). Ne pas monter sans migrer la box.
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Check for missing migrations
        env:
          STATE: TEST
          SECRET_KEY: test-secret-key
        run: python manage.py makemigrations --check --dry-run

      - name: Run tests
        env:
          STATE: TEST
          SECRET_KEY: test-secret-key
        run: pytest -q
```

- [ ] **Step 2 : Vérifier localement ce que la CI va exécuter**

```bash
STATE=TEST SECRET_KEY=test-secret-key .venv/Scripts/python manage.py makemigrations --check --dry-run
STATE=TEST SECRET_KEY=test-secret-key .venv/Scripts/pytest -q
```
Expected: les deux commandes sortent en 0.

- [ ] **Step 3 : Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: run migration check and tests on push and pull request"
```

- [ ] **Step 4 : Créer le dépôt GitHub et pousser**

```bash
gh repo create Foxugly/billing_server --public --source=. --remote=origin
git push -u origin main
```

**Pourquoi public** — vérifié le 2026-07-28 : tous les dépôts applicatifs de l'org sont publics
(seul `foxugly-ops` est privé), et la box clone en **HTTPS anonyme** (`sudo -u django git remote -v`
sur Poker → `https://github.com/Foxugly/Poker_server.git`, aucun credential helper, aucune clé SSH
dans `/home/django/.ssh`). Un dépôt privé casserait le `git fetch` du déploiement et imposerait
d'introduire une clé de déploiement — un mécanisme que la flotte n'a pas. Le modèle de sécurité ne
repose pas sur le secret du code : aucun secret n'est commité, tout vit en SSM.

- [ ] **Step 5 : Vérifier que la CI est verte**

```bash
gh run list --limit 1
gh run watch
```
Expected: conclusion `success`. **Attention** (leçon flotte) : `gh pr checks --watch` sort en 0
même en cas d'échec — toujours relire la conclusion explicitement, ne jamais se fier au code de
sortie de `--watch`.

---

### Task 4 : Artefacts de déploiement versionnés

Tout ce que **root** installera est versionné ici et installé depuis le **blob git**, jamais
copié depuis l'arbre `django` (§3.10 — un gunicorn compromis pourrait sinon modifier un script
exécuté en root).

**Files:**
- Create: `gunicorn.conf.py`
- Create: `deploy/fetch-env-from-ssm.sh`
- Create: `deploy/systemd/billing-env-fetch.service`
- Create: `deploy/systemd/billing-gunicorn.service`
- Create: `deploy/nginx/billing-api.conf`
- Create: `deploy/deploy.sh`
- Create: `deploy/DEPLOY.md`

**Interfaces:**
- Consumes: `config/wsgi.py` (Task 1).
- Produces: les chemins que la Task 5 installera —
  `/usr/local/sbin/billing-env-fetch.sh`, `/etc/systemd/system/billing-{env-fetch,gunicorn}.service`,
  `/etc/nginx/sites-available/billing-api.conf`, et `deploy/deploy.sh` exécuté sous `django`.

- [ ] **Step 1 : Configuration gunicorn**

`gunicorn.conf.py` :

```python
# Le bind par défaut DOIT être le port assigné au site (§3.4) : ne jamais laisser
# un défaut qui entrerait en collision avec un autre site de la flotte.
bind = "127.0.0.1:8007"
workers = 3
timeout = 60
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
loglevel = "info"
```

- [ ] **Step 2 : Script de récupération des secrets**

`deploy/fetch-env-from-ssm.sh` :

```bash
#!/usr/bin/env bash
# =============================================================================
# Foxugly Billing — récupère l'environnement depuis AWS SSM vers un tmpfs.
#
# Exécuté en root par billing-env-fetch.service (oneshot) au boot, AVANT
# gunicorn. Le fichier vit dans /run (tmpfs) : jamais sur disque, re-fetché à
# chaque boot. Source de vérité = SSM /billing/prod/* (eu-west-1), lu via le
# rôle d'instance EC2 par IMDS (aucune clé AWS sur disque).
#
# §3.10 : ce script tourne en root, il ne doit donc PAS vivre dans l'arbre
# écrivable par django. Il est installé root:root 0755 dans
# /usr/local/sbin/billing-env-fetch.sh. Ce fichier-ci est la source versionnée,
# jamais la cible d'exécution.
# =============================================================================
set -euo pipefail
umask 077   # les fichiers temporaires contiennent brièvement des secrets déchiffrés.

SSM_PREFIX="/billing/prod"
AWS_REGION="eu-west-1"
RUN_DIR="/run/billing"
ENV_FILE="$RUN_DIR/.env"
TMP_FILE="$RUN_DIR/.env.tmp"
RAW_FILE="$RUN_DIR/.ssm.json"
OWNER="django:www-data"

mkdir -p "$RUN_DIR"
# 750 root:www-data — root l'écrit ; django (groupe www-data) peut traverser ;
# le .env reste 640 pour que son contenu reste protégé (§3.5).
chmod 750 "$RUN_DIR"
chown root:www-data "$RUN_DIR"

aws ssm get-parameters-by-path \
    --path "$SSM_PREFIX" \
    --recursive \
    --with-decryption \
    --region "$AWS_REGION" \
    --output json > "$RAW_FILE"

python3 - "$SSM_PREFIX" "$TMP_FILE" "$RAW_FILE" <<'PY'
import json, sys

prefix, tmp_path, raw_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(raw_path) as fh:
    params = json.load(fh).get("Parameters", [])

if not params:
    sys.stderr.write(f"ERROR: no parameters under {prefix}; refusing to write an empty env.\n")
    sys.exit(1)

lines = []
for p in params:
    key = p["Name"][len(prefix):].lstrip("/")
    value = p["Value"].strip("\r\n")
    if "\n" in value or "\r" in value:
        sys.stderr.write(f"ERROR: value for {key} contains an internal newline; refusing.\n")
        sys.exit(1)
    lines.append(f"{key}={value}")

with open(tmp_path, "w") as fh:
    fh.write("\n".join(sorted(lines)) + "\n")
PY

rm -f "$RAW_FILE"

if [ ! -s "$TMP_FILE" ]; then
    echo "ERROR: assembled env file is empty; keeping previous $ENV_FILE." >&2
    rm -f "$TMP_FILE"
    exit 1
fi

chmod 640 "$TMP_FILE"
chown "$OWNER" "$TMP_FILE"
mv -f "$TMP_FILE" "$ENV_FILE"

echo "Wrote $(wc -l < "$ENV_FILE") variables to $ENV_FILE."
```

- [ ] **Step 3 : Units systemd**

`deploy/systemd/billing-env-fetch.service` :

```ini
[Unit]
Description=Foxugly Billing — fetch environment from AWS SSM into /run/billing/.env
After=network-online.target
Wants=network-online.target
Before=billing-gunicorn.service

[Service]
# oneshot + RemainAfterExit : tourne une fois au boot et reste "active".
# Un déploiement de code ne redémarre PAS cette unit. Pour recharger les valeurs
# SSM : `sudo systemctl restart billing-env-fetch && sudo systemctl restart billing-gunicorn`.
Type=oneshot
RemainAfterExit=yes
# Force le rôle d'instance EC2 (foxugly-fleet-ec2) via IMDS en neutralisant les
# clés statiques certbot-route53 de /root/.aws, qui sinon masquent le rôle (§3.5).
Environment=AWS_SHARED_CREDENTIALS_FILE=/dev/null
Environment=AWS_CONFIG_FILE=/dev/null
Environment=AWS_REGION=eu-west-1
# §3.10 : script exécuté en root, donc HORS de l'arbre django.
ExecStart=/usr/local/sbin/billing-env-fetch.sh
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

`deploy/systemd/billing-gunicorn.service` :

```ini
[Unit]
Description=Foxugly Billing — gunicorn (WSGI)
After=network.target billing-env-fetch.service postgresql.service
Requires=billing-env-fetch.service

[Service]
User=django
Group=www-data
UMask=0027
WorkingDirectory=/var/www/django_websites/billing_server
EnvironmentFile=/run/billing/.env
ExecStart=/var/www/django_websites/billing_server/.venv/bin/gunicorn \
    --config /var/www/django_websites/billing_server/gunicorn.conf.py \
    config.wsgi:application
Restart=always
RestartSec=5
KillMode=mixed
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4 : Vhost nginx**

`deploy/nginx/billing-api.conf` — bloc 443 manuel sur le wildcard partagé, jamais de certbot
par sous-domaine (§3.6) :

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name billing-api.foxugly.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name billing-api.foxugly.com;

    # Certificat wildcard partagé par toute la flotte (lineage foxugly.com).
    ssl_certificate     /etc/letsencrypt/live/foxugly.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/foxugly.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    # Les webhooks Stripe (lot L2) peuvent porter des payloads volumineux.
    client_max_body_size 2m;

    location / {
        proxy_pass http://127.0.0.1:8007;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }

    location /static/ {
        alias /var/www/django_websites/billing_server/staticfiles/;
        access_log off;
        expires 30d;
    }
}
```

- [ ] **Step 5 : Script de déploiement applicatif**

`deploy/deploy.sh` :

```bash
#!/usr/bin/env bash
# =============================================================================
# Foxugly Billing — script de déploiement (exécuté sous 'django' via OIDC->SSM).
#   /var/www/django_websites/billing_server/deploy/deploy.sh
# =============================================================================
set -euo pipefail
umask 027   # nouveaux dirs 750 / fichiers 640 depuis git/pip/collectstatic (§3.1/§3.2)

APP_DIR="/var/www/django_websites/billing_server"
VENV="$APP_DIR/.venv"

cd "$APP_DIR"

echo ">>> Installing dependencies..."
"$VENV/bin/pip" install --quiet -r requirements.txt

# Charger l'env fetché depuis SSM pour que manage.py ait SECRET_KEY, STATE, DB_*.
# Parsé littéralement (key=value), PAS via `source` : les valeurs peuvent contenir
# des caractères que le shell interpréterait (comportement d'EnvironmentFile).
ENV_FILE="/run/billing/.env"
if [ -f "$ENV_FILE" ]; then
    echo ">>> Loading env from $ENV_FILE..."
    while IFS='=' read -r _k _v || [ -n "$_k" ]; do
        case "$_k" in ''|\#*) continue ;; esac
        export "$_k=$_v"
    done < "$ENV_FILE"
    unset _k _v
else
    echo "WARNING: $ENV_FILE missing — has billing-env-fetch run? Trying without it." >&2
fi

echo ">>> Running migrations..."
"$VENV/bin/python" manage.py migrate --noinput

echo ">>> Collecting static files..."
"$VENV/bin/python" manage.py collectstatic --noinput

echo ">>> Normalizing permissions (dirs 750 / files 640, no o-rwx, no g-w)..."
# chown AVANT chmod : l'ordre inverse verrouille django hors de son propre venv (§3.1).
chown -R django:www-data "$APP_DIR"
chmod -R g-w,o-rwx "$APP_DIR"

echo ">>> Restarting services..."
sudo /bin/systemctl restart billing-gunicorn

echo ">>> Deploy complete."
```

- [ ] **Step 6 : Vérifier la syntaxe des scripts**

```bash
bash -n deploy/deploy.sh
bash -n deploy/fetch-env-from-ssm.sh
```
Expected: aucune sortie, code 0 pour les deux.

- [ ] **Step 7 : Documenter le déploiement**

`deploy/DEPLOY.md` :

```markdown
# Déploiement — billing

Modèle flotte : GitHub Actions → OIDC → SSM (OPERATIONS.md §3.11). Aucun `git pull` manuel sur
la box, aucun push GitHub depuis l'EC2.

| Élément | Valeur |
|---|---|
| Arbre | `/var/www/django_websites/billing_server` |
| Port | `127.0.0.1:8007` |
| Units | `billing-env-fetch` (oneshot root), `billing-gunicorn` |
| Env | `/run/billing/.env` (tmpfs, 640 django:www-data) ← SSM `/billing/prod` |
| vhost | `billing-api.foxugly.com` → `deploy/nginx/billing-api.conf` |
| Rôle OIDC | `billing-deploy`, épinglé sur `repo:Foxugly/billing_server:environment:production` |

## Recharger les secrets après un changement en SSM

    sudo systemctl restart billing-env-fetch
    sudo systemctl restart billing-gunicorn

## Diagnostic

    systemctl status billing-gunicorn
    journalctl -u billing-gunicorn -n 100 --no-pager
    curl -s https://billing-api.foxugly.com/health/
```

- [ ] **Step 8 : Commit**

```bash
git add gunicorn.conf.py deploy/
git commit -m "feat(deploy): versioned systemd units, nginx vhost, env fetch and deploy script"
```

---

### Task 5 : Job de déploiement OIDC → SSM

**Files:**
- Modify: `.github/workflows/deploy.yml` (ajouter le job `deploy` après le job `test`)

**Interfaces:**
- Consumes: le job `test` (Task 3) ; les artefacts de la Task 4 aux chemins exacts
  `deploy/fetch-env-from-ssm.sh`, `deploy/systemd/billing-{env-fetch,gunicorn}.service`,
  `deploy/nginx/billing-api.conf`, `deploy/deploy.sh`.
- Produces: un déploiement automatique à chaque push sur `main`. Consomme les secrets de dépôt
  `AWS_DEPLOY_ROLE_ARN` et `EC2_INSTANCE_ID` créés en Task 6.

- [ ] **Step 1 : Ajouter le job `deploy`**

Ajouter à la fin de `.github/workflows/deploy.yml` :

```yaml
  deploy:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    environment: production
    permissions:
      id-token: write # émet le jeton OIDC pour assumer le rôle de déploiement
      contents: read

    steps:
      # OIDC -> SSM (pas de clé SSH statique). AWS-RunShellScript tourne en root :
      # il installe chaque artefact privilégié depuis le BLOB GIT COMMITÉ — jamais
      # par cp depuis l'arbre django (§3.10 / §3.11) — puis lance deploy.sh en django.
      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: eu-west-1
          role-session-name: gh-actions-${{ github.run_id }}

      - name: Trigger deploy on EC2 via SSM
        id: ssm
        env:
          INSTANCE_ID: ${{ secrets.EC2_INSTANCE_ID }}
        run: |
          cat > /tmp/ssm-params.json <<'EOF'
          { "commands": [
            "set -eu",
            "export HOME=/root",
            "APP=/var/www/django_websites/billing_server",
            "git config --global --get-all safe.directory 2>/dev/null | grep -qx $APP || git config --global --add safe.directory $APP",
            "sudo -u django bash -c 'umask 027; cd '$APP' && git fetch --quiet origin main && git reset --hard --quiet origin/main'",
            "git -C $APP show origin/main:deploy/fetch-env-from-ssm.sh > /usr/local/sbin/billing-env-fetch.sh",
            "chown root:root /usr/local/sbin/billing-env-fetch.sh && chmod 0755 /usr/local/sbin/billing-env-fetch.sh",
            "for u in billing-env-fetch billing-gunicorn; do git -C $APP show origin/main:deploy/systemd/$u.service > /etc/systemd/system/$u.service; done",
            "git -C $APP show origin/main:deploy/nginx/billing-api.conf > /etc/nginx/sites-available/billing-api.conf",
            "ln -sf ../sites-available/billing-api.conf /etc/nginx/sites-enabled/billing-api.conf",
            "systemctl daemon-reload",
            "systemctl enable billing-env-fetch billing-gunicorn",
            "systemctl restart billing-env-fetch",
            "sudo -u django bash $APP/deploy/deploy.sh",
            "nginx -t && systemctl reload nginx"
          ], "executionTimeout": ["900"] }
          EOF
          CMD_ID=$(aws ssm send-command \
            --instance-ids "$INSTANCE_ID" \
            --document-name AWS-RunShellScript \
            --comment "Deploy billing ${{ github.sha }}" \
            --cloud-watch-output-config CloudWatchOutputEnabled=true \
            --parameters file:///tmp/ssm-params.json \
            --query Command.CommandId --output text)
          echo "cmd_id=$CMD_ID" >> "$GITHUB_OUTPUT"

      - name: Wait for SSM command to finish
        env:
          INSTANCE_ID: ${{ secrets.EC2_INSTANCE_ID }}
          CMD_ID: ${{ steps.ssm.outputs.cmd_id }}
        run: |
          for _ in $(seq 1 180); do
            STATUS=$(aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null || echo Pending)
            case "$STATUS" in
              Success)
                aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --query StandardOutputContent --output text
                exit 0 ;;
              Failed|Cancelled|TimedOut)
                echo "Deploy $STATUS"
                aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --query StandardOutputContent --output text
                aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --query StandardErrorContent --output text
                exit 1 ;;
              *) sleep 5 ;;
            esac
          done
          echo "Timed out waiting for SSM command after 15 minutes"; exit 1
```

- [ ] **Step 2 : Valider le YAML**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/deploy.yml')); print('ok')"`
Expected: `ok`.

- [ ] **Step 3 : Commit (sans pousser — le provisioning de la Task 6 doit précéder)**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci(deploy): deploy to EC2 over OIDC to SSM on push to main"
```

**Ne pas pousser maintenant.** Le job échouerait : ni le rôle IAM, ni les secrets de dépôt, ni
l'arbre sur la box n'existent encore. La Task 6 les crée, et se termine par le push.

---

### Task 6 : Provisionnement AWS et serveur (hors code)

Cette tâche ne modifie aucun fichier du dépôt : elle prépare l'infrastructure. Elle suit la
checklist OPERATIONS.md §3.12 dans l'ordre. **L'administration IAM doit être faite depuis le
poste, PAS depuis la box** : sur la box, un `aws` manuel s'authentifie comme l'utilisateur IAM
`certbot-route53`, qui n'a aucun droit IAM/SSM, et échoue avec des `AccessDenied` trompeurs (§3.5).

**Interfaces:**
- Consumes: `deploy/` et le workflow (Tasks 4-5).
- Produces: les secrets de dépôt `AWS_DEPLOY_ROLE_ARN` / `EC2_INSTANCE_ID` consommés par la
  Task 5, la base `billing`, le préfixe SSM `/billing/prod`, et l'arbre serveur.

- [ ] **Step 1 : Enregistrement DNS**

Créer dans Route53 (zone `foxugly.com`) un enregistrement **A** `billing-api.foxugly.com` vers
l'IP publique de l'EC2, identique à celui de `poker-api.foxugly.com`. Le certificat wildcard
`*.foxugly.com` couvre déjà ce nom : **ne pas lancer certbot** (§3.6, mémoire `fleet-tls-single-wildcard`).

Vérifier : `nslookup billing-api.foxugly.com` renvoie l'IP de la box.

- [ ] **Step 2 : Base PostgreSQL**

```bash
ssh -i "/c/Users/Renaud/Dropbox/key/foxugly.com.pem" ubuntu@ec2-54-229-220-110.eu-west-1.compute.amazonaws.com \
  "sudo -u postgres psql -c \"CREATE ROLE billing LOGIN PASSWORD '<mot-de-passe-généré>';\" \
   && sudo -u postgres psql -c 'CREATE DATABASE billing OWNER billing;' \
   && sudo -u postgres psql -d billing -c 'ALTER SCHEMA public OWNER TO billing;'"
```

Générer le mot de passe localement (`python -c "import secrets; print(secrets.token_urlsafe(32))"`)
et le conserver pour l'étape suivante.

- [ ] **Step 3 : Seeder les paramètres SSM**

Depuis le poste, avec un profil AWS administrateur. Noms **nus**, secrets en `SecureString` :

```bash
REGION=eu-west-1
put() { aws ssm put-parameter --region $REGION --name "/billing/prod/$1" --value "$2" --type "$3" --overwrite; }

put STATE PROD String
put SECRET_KEY "$(python -c 'import secrets; print(secrets.token_urlsafe(50))')" SecureString
put ALLOWED_HOSTS "billing-api.foxugly.com,127.0.0.1,localhost" String
put CSRF_TRUSTED_ORIGINS "https://billing-api.foxugly.com,https://billing.foxugly.com" String
put CORS_ALLOWED_ORIGINS "https://billing.foxugly.com" String
put FRONTEND_BASE_URL "https://billing.foxugly.com" String
put DB_ENGINE postgresql String
put DB_HOST 127.0.0.1 String
put DB_PORT 5432 String
put DB_NAME billing String
put DB_USER billing String
put DB_PASSWORD "<le mot de passe de l'étape 2>" SecureString
```

Vérifier qu'aucune valeur n'a été seedée en ciphertext KMS brut (piège flotte connu : une valeur
qui commence par `AQIC…` casse silencieusement la fonctionnalité) :

```bash
aws ssm get-parameters-by-path --region eu-west-1 --path /billing/prod --recursive --with-decryption \
  --query "Parameters[?starts_with(Value, 'AQIC')].Name" --output text
```
Expected: sortie vide.

- [ ] **Step 4 : Autoriser le rôle d'instance à lire ce préfixe**

Le rôle partagé `foxugly-fleet-ec2` accorde SSM **par préfixe** : il faut ajouter `/billing/prod`.
Pour `GetParametersByPath`, la ressource doit lister **le nœud ET les enfants** — le `/*` seul
n'autorise pas la requête par chemin (§3.5) :

```json
{
  "Effect": "Allow",
  "Action": ["ssm:GetParametersByPath", "ssm:GetParameters"],
  "Resource": [
    "arn:aws:ssm:eu-west-1:<account-id>:parameter/billing/prod",
    "arn:aws:ssm:eu-west-1:<account-id>:parameter/billing/prod/*"
  ]
}
```

Plus `kms:Decrypt` sur `alias/aws/ssm`, si ce n'est pas déjà couvert par la politique existante.

- [ ] **Step 5 : Rôle de déploiement OIDC**

Créer le rôle IAM `billing-deploy`, confiance **épinglée** (pas de wildcard) :

```json
{
  "Effect": "Allow",
  "Principal": {"Federated": "arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com"},
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {
      "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub": "repo:Foxugly/billing_server:environment:production"
    }
  }
}
```

Permissions au minimum : `ssm:SendCommand` sur l'instance et sur le document
`AWS-RunShellScript`, plus `ssm:GetCommandInvocation`. **Pas** de `s3:PutObject` : ce lot ne
livre aucun bundle (ce sera le cas du frontend au lot L5, avec son propre rôle).

Puis créer l'environnement GitHub `production` et les secrets de dépôt :

```bash
gh api -X PUT repos/Foxugly/billing_server/environments/production
gh secret set AWS_DEPLOY_ROLE_ARN --body "arn:aws:iam::<account-id>:role/billing-deploy"
gh secret set EC2_INSTANCE_ID --body "<i-…>"
```

- [ ] **Step 6 : sudoers au minimum de privilège**

Créer `/etc/sudoers.d/billing-deploy` en root sur la box, `0440 root:root`. Garder le chemin
`/bin/systemctl` : sudo compare les chemins littéralement, malgré l'usrmerge (§3.7).

```
django ALL=(root) NOPASSWD: /bin/systemctl restart billing-gunicorn, /bin/systemctl restart billing-env-fetch, /usr/sbin/nginx -t, /bin/systemctl reload nginx
Defaults!/bin/systemctl !setenv, !env_keep
```

Valider **avant** d'activer : `sudo visudo -cf /etc/sudoers.d/billing-deploy`.

- [ ] **Step 7 : Cloner l'arbre sur la box, en tant que `django`**

Installer le code **en tant que `django`**, jamais en root — c'est la cause racine de
l'incident de propriété du 2026-06-03 (§3.2, §4) :

```bash
sudo -u django bash -c 'umask 027; git clone https://github.com/Foxugly/billing_server.git /var/www/django_websites/billing_server'
sudo -u django bash -c 'cd /var/www/django_websites/billing_server && python3.12 -m venv .venv'
```

- [ ] **Step 8 : Pousser et vérifier le premier déploiement**

```bash
git push origin main
gh run watch
```

Expected: run `success`. Puis :

```bash
curl -s https://billing-api.foxugly.com/health/
```
Expected: `{"status": "ok", "database": "ok"}`.

- [ ] **Step 9 : Vérifications de conformité (§3.12 étape 9)**

```bash
ssh -i "/c/Users/Renaud/Dropbox/key/foxugly.com.pem" ubuntu@ec2-54-229-220-110.eu-west-1.compute.amazonaws.com \
  "sudo find /var/www/django_websites/billing_server ! -type l \( -perm /020 -o -perm /004 \) | head; \
   echo '--- sudo ---'; sudo -l -U django | grep billing; \
   echo '--- run ---'; sudo ls -ld /run/billing /run/billing/.env; \
   echo '--- units ---'; systemctl is-active billing-env-fetch billing-gunicorn"
```

Expected : aucun fichier listé par le `find` (ni group-write ni other-read) ; les grants sudo
limités aux commandes de l'étape 6 ; `/run/billing` en `750 root:www-data` et `.env` en
`640 django:www-data` ; les deux units `active`.

- [ ] **Step 10 : Créer le compte opérateur**

```bash
ssh … "sudo systemd-run --uid=django --property=EnvironmentFile=/run/billing/.env \
  --working-directory=/var/www/django_websites/billing_server --pty --quiet \
  /var/www/django_websites/billing_server/.venv/bin/python manage.py createsuperuser --email rvilain@foxugly.com"
```

(`systemd-run` est la recette flotte pour exécuter une commande de gestion avec l'environnement
SSM chargé — voir la mémoire `fleet-django-migration-postgres-gotchas`.)

---

### Task 7 : Supervision et inscription à l'inventaire

**Files:**
- Modify: `config/settings/base.py` (initialisation Sentry)
- Modify: `requirements.txt` (déjà présent — vérifier `sentry-sdk[django]`)
- Modify (repo `foxugly-ops`) : `OPERATIONS.md` §2 inventaire des sites

**Interfaces:**
- Consumes: le service déployé et vert (Task 6).
- Produces: le projet Sentry `billing-backend`, un monitor UptimeRobot, et la ligne d'inventaire
  qui rend le site officiellement connu de la flotte.

- [ ] **Step 1 : Écrire le test de garde de la configuration Sentry**

`config/tests/__init__.py` (fichier vide) et `config/tests/test_sentry_settings.py` :

```python
from django.conf import settings


def test_sentry_is_not_initialised_outside_prod():
    """En TEST/DEV, aucun DSN ne doit être requis ni aucun événement envoyé."""
    assert settings.STATE != "PROD"
    assert settings.SENTRY_DSN == ""
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `.venv/Scripts/pytest config -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'SENTRY_DSN'`.

- [ ] **Step 3 : Implémenter l'initialisation Sentry**

Ajouter à la fin de `config/settings/base.py` :

```python
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
```

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

Run: `.venv/Scripts/pytest -q`
Expected: PASS — 8 passed.

- [ ] **Step 5 : Créer le projet Sentry et seeder le DSN**

Créer le projet `billing-backend` dans l'org `foxugly-srl` (région `de.sentry.io`), puis :

```bash
aws ssm put-parameter --region eu-west-1 --name /billing/prod/SENTRY_DSN \
  --value "<dsn>" --type String --overwrite
aws ssm put-parameter --region eu-west-1 --name /billing/prod/SENTRY_ENVIRONMENT \
  --value production --type String --overwrite
```

Puis recharger : `sudo systemctl restart billing-env-fetch && sudo systemctl restart billing-gunicorn`.

- [ ] **Step 6 : Monitor UptimeRobot**

Créer un monitor HTTP sur `https://billing-api.foxugly.com/health/`, avec assertion du mot-clé
`"status": "ok"`, intervalle 5 minutes. Alertes dashboard uniquement, comme le reste de la flotte.

- [ ] **Step 7 : Commit du code**

```bash
git add config/settings/base.py config/tests/
git commit -m "feat(observability): initialise Sentry under STATE=PROD only"
git push origin main
```

- [ ] **Step 8 : Inscrire le site à l'inventaire de la flotte**

Dans le repo `foxugly-ops` (`D:\Projects\PycharmProjects\foxugly-ops`), ajouter à la table
§2 « Site inventory » d'`OPERATIONS.md` :

```markdown
| billing | `billing_server` (+ `billing_frontend`) | 8007 | billing-api.foxugly.com / billing.foxugly.com | billing-gunicorn, billing-env-fetch |
```

Commiter et pousser, puis re-synchroniser la copie de la box
(`/var/www/django_websites/OPERATIONS.md`, `640 django:www-data`) — le repo fait foi.

```bash
cd /d/Projects/PycharmProjects/foxugly-ops
git add OPERATIONS.md
git commit -m "docs: add billing to the site inventory (port 8007)"
git push
```

---

## Critère de sortie du lot L1

Toutes les affirmations ci-dessous doivent être **vérifiées par une commande**, pas supposées :

1. `curl -s https://billing-api.foxugly.com/health/` → `{"status": "ok", "database": "ok"}`
2. Un push sur `main` déclenche un run GitHub Actions dont la conclusion est `success`
3. Le `find` de permissions ne renvoie aucun fichier
4. `sudo -l -U django` ne montre que les quatre commandes prévues pour `billing`
5. `systemctl is-active billing-env-fetch billing-gunicorn` → `active` / `active`
6. Le monitor UptimeRobot est vert
7. `manage.py makemigrations --check --dry-run` sort en 0 en CI

---

## Lots suivants (plans à écrire, un par lot)

| Lot | Contenu | Pourquoi un plan séparé |
|---|---|---|
| **L2** | dj-stripe, modèles `App`/`Plan`/`AppCustomer`/`Entitlement`/`EntitlementDelivery`, Django admin, webhook Stripe, `recompute_entitlement` | C'est le cœur métier ; il se teste entièrement hors ligne sur fixtures et se livre seul |
| **L3** | API S2S signée HMAC, Celery + Redis db4, file de livraison et retries, `sync_entitlements` | Ajoute une dépendance d'infrastructure (Celery) et un contrat public |
| **L4** | Migration de Poker en consommateur | Touche un autre dépôt et un site en production |
| **L5** | Console Angular + rôle `billing-frontend-deploy` + bucket S3 | Autre dépôt, autre pipeline |
| **L6** | Mise en service Stripe réelle et cutover | Runbook d'exploitation, pas du code |
| **L7** | Facturation directe (consulting) | Fonctionnalité indépendante, postérieure à la mise en service |

Chaque plan sera écrit **juste avant son exécution**, à la lumière de ce que le lot précédent
aura appris. Écrire les sept maintenant produirait surtout de la fiction.
