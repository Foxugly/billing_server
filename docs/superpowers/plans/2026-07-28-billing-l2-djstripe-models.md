# Billing — Lot L2 : dj-stripe, modèles métier et calcul des droits

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Poser le cœur métier du service : le miroir Stripe (dj-stripe), les cinq modèles
`App` / `Plan` / `AppCustomer` / `Entitlement` / `EntitlementDelivery`, la fonction
`recompute_entitlement()` qui dérive un droit depuis l'état Stripe, l'admin Django pour tout
piloter, et la réception des webhooks Stripe.

**Architecture:** dj-stripe mire les objets Stripe en base et n'est jamais modifié à la main.
Au-dessus, une app Django `core` porte nos modèles métier. Un handler branché sur les événements
dj-stripe recalcule l'`Entitlement` concerné. **La livraison aux applications n'est PAS dans ce
lot** (lot L3) : ici on se contente de créer l'`EntitlementDelivery` en `pending`.

**Tech Stack:** dj-stripe 2.11, Django 6.0.6, DRF, pytest. Aucun appel réseau vers Stripe dans
les tests — uniquement des fixtures JSON et des mocks.

**Spec de référence :** `docs/superpowers/specs/2026-07-28-billing-central-design.md` §5, §6.2, §6.6.

**Branche :** `feat/l2-djstripe-models` → PR vers `main`. **Ne pas pousser directement sur `main`**
(auto-déploiement).

## Global Constraints

- Reprendre les contraintes du lot L1 (Python 3.12 en CI, Django 6.0.6, `STATE`, noms SSM nus,
  permissions 750/640, commits en anglais).
- **`DJSTRIPE_FOREIGN_KEY_TO_FIELD = "id"`** — valeur recommandée pour une installation neuve.
  `"djstripe_id"` n'est là que pour les installations historiques ; le choix est **irréversible**
  sans migration douloureuse, il se fait donc maintenant.
- **`STRIPE_LIVE_MODE` doit être un booléen**, pas une chaîne — `env.bool`, jamais `env`.
- Les clés Stripe restent **absentes de SSM dans ce lot**. Rien ne les exige : les tests
  n'appellent pas Stripe, et le service démarre sans. Elles arrivent au lot L6.
- **Le webhook utilise le mécanisme natif de dj-stripe** (≥ 2.7) : les endpoints sont créés depuis
  l'admin, l'URL contient un UUID généré (non devinable de l'extérieur) et chaque endpoint porte
  son propre secret. On **n'écrit pas** de vue webhook maison — le spec parlait de
  `/api/v1/stripe/webhook/`, c'est remplacé par `path("stripe/", include("djstripe.urls"))`.
- **Aucun objet dj-stripe n'est modifié par notre code.** Stripe reste la source de vérité pour
  l'argent ; nous ne faisons que lire son miroir.

---

### Task 1 : Installer dj-stripe et faire tourner ses migrations

**Files:**
- Modify: `requirements.txt`
- Modify: `config/settings/base.py`
- Modify: `config/urls.py`
- Test: `config/tests/test_djstripe_settings.py`

**Interfaces:**
- Consumes: le projet du lot L1.
- Produces: l'app `djstripe` installée et migrée ; les settings `STRIPE_LIVE_MODE` (bool),
  `STRIPE_TEST_SECRET_KEY`, `STRIPE_LIVE_SECRET_KEY`, `DJSTRIPE_FOREIGN_KEY_TO_FIELD`.
  Les modèles `djstripe.models.{Customer, Price, Product, Subscription, Invoice, Event}` deviennent
  importables par les tâches suivantes.

- [ ] **Step 1 : Écrire le test qui échoue**

`config/tests/test_djstripe_settings.py` :

```python
import pytest
from django.apps import apps
from django.conf import settings


def test_djstripe_is_installed():
    assert apps.is_installed("djstripe")


def test_foreign_key_to_field_is_id_for_a_fresh_install():
    """Choix irréversible : "id" est la valeur recommandée hors migration historique."""
    assert settings.DJSTRIPE_FOREIGN_KEY_TO_FIELD == "id"


def test_live_mode_is_a_boolean_not_a_string():
    """Une chaîne "False" serait vraie : dj-stripe basculerait en live sans prévenir."""
    assert isinstance(settings.STRIPE_LIVE_MODE, bool)
    assert settings.STRIPE_LIVE_MODE is False


@pytest.mark.django_db
def test_djstripe_models_are_migrated():
    from djstripe.models import Customer

    assert Customer.objects.count() == 0
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `.venv/Scripts/pytest config -q`
Expected: FAIL — `djstripe` n'est pas installée.

- [ ] **Step 3 : Ajouter la dépendance**

Dans `requirements.txt`, après `drf-spectacular` :

```
dj-stripe==2.11.0
```

Run: `.venv/Scripts/pip install -r requirements.txt`

- [ ] **Step 4 : Configurer**

Dans `config/settings/base.py`, ajouter `"djstripe",` à `INSTALLED_APPS` **avant**
`"accounts.apps.AccountsConfig",`, puis ce bloc juste après `AUTH_USER_MODEL` :

```python
# --- Stripe (dj-stripe) ---------------------------------------------------------
# Les clés vivent en base (modèle djstripe.APIKey) ou en env ; elles sont absentes
# en dev/test et le service démarre sans. Elles seront seedées en SSM au lot L6.
STRIPE_TEST_SECRET_KEY = env("STRIPE_TEST_SECRET_KEY", default="")
STRIPE_LIVE_SECRET_KEY = env("STRIPE_LIVE_SECRET_KEY", default="")
# env.bool et non env : la chaîne "False" serait vraie, et dj-stripe basculerait
# en mode live sans le moindre avertissement.
STRIPE_LIVE_MODE = env.bool("STRIPE_LIVE_MODE", default=False)
# "id" est la valeur recommandée pour une installation neuve ; "djstripe_id"
# n'existe que pour les installations antérieures à dj-stripe 2.4. Le changer
# après coup n'a pas de chemin de migration.
DJSTRIPE_FOREIGN_KEY_TO_FIELD = "id"
```

Dans `config/urls.py`, ajouter :

```python
    path("stripe/", include("djstripe.urls", namespace="djstripe")),
```

- [ ] **Step 5 : Générer et appliquer les migrations**

Run: `.venv/Scripts/python manage.py migrate`
Expected: les migrations `djstripe` s'appliquent sans erreur.

Run: `.venv/Scripts/python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`.

- [ ] **Step 6 : Lancer les tests**

Run: `.venv/Scripts/pytest -q`
Expected: PASS — 12 passed.

- [ ] **Step 7 : Commit**

```bash
git add -A
git commit -m "feat(billing): install dj-stripe as the Stripe mirror"
```

---

### Task 2 : Les tenants — modèles `App` et `Plan`

**Files:**
- Create: `core/__init__.py`, `core/apps.py`, `core/models.py`, `core/migrations/__init__.py`
- Modify: `config/settings/base.py` (INSTALLED_APPS)
- Test: `core/tests/__init__.py`, `core/tests/test_models_app_plan.py`

**Interfaces:**
- Consumes: `djstripe.models.Price` (Task 1).
- Produces: `core.models.App` (champs `slug`, `name`, `base_url`, `entitlement_path`,
  `shared_secret`, `active`) et `core.models.Plan` (`app`, `code`, `name`, `description`,
  `price_monthly`, `price_yearly`, `quotas`, `sort_order`, `public`, `active`), plus
  `Plan.price_for(interval)` → `djstripe.models.Price | None`.

- [ ] **Step 1 : Écrire les tests qui échouent**

`core/tests/test_models_app_plan.py` :

```python
import pytest
from django.db import IntegrityError

from core.models import App, Plan


@pytest.mark.django_db
def test_app_slug_is_unique():
    App.objects.create(slug="poker", name="Delegation Poker", base_url="https://poker-api.foxugly.com")

    with pytest.raises(IntegrityError):
        App.objects.create(slug="poker", name="Doublon", base_url="https://x.foxugly.com")


@pytest.mark.django_db
def test_app_has_a_default_entitlement_path():
    app = App.objects.create(slug="poker", name="Poker", base_url="https://poker-api.foxugly.com")

    assert app.entitlement_path == "/api/billing/entitlement/"
    assert app.active is True


@pytest.mark.django_db
def test_app_entitlement_url_joins_base_and_path():
    app = App.objects.create(
        slug="poker", name="Poker", base_url="https://poker-api.foxugly.com/"
    )

    assert app.entitlement_url == "https://poker-api.foxugly.com/api/billing/entitlement/"


@pytest.mark.django_db
def test_plan_code_is_unique_per_app_but_not_across_apps():
    poker = App.objects.create(slug="poker", name="Poker", base_url="https://a.foxugly.com")
    tm = App.objects.create(slug="tm", name="TM", base_url="https://b.foxugly.com")

    Plan.objects.create(app=poker, code="team1", name="1 équipe", quotas={"teams": 1})
    Plan.objects.create(app=tm, code="team1", name="1 équipe", quotas={"teams": 1})

    with pytest.raises(IntegrityError):
        Plan.objects.create(app=poker, code="team1", name="Doublon", quotas={})


@pytest.mark.django_db
def test_plan_price_for_returns_none_when_the_interval_is_not_configured():
    app = App.objects.create(slug="poker", name="Poker", base_url="https://a.foxugly.com")
    plan = Plan.objects.create(app=app, code="team1", name="1 équipe", quotas={"teams": 1})

    assert plan.price_for("monthly") is None
    assert plan.price_for("yearly") is None
    assert plan.price_for("n-importe-quoi") is None
```

`core/tests/__init__.py` : fichier vide.

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `.venv/Scripts/pytest core -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core'`.

- [ ] **Step 3 : Créer l'app et les modèles**

`core/__init__.py`, `core/migrations/__init__.py` : fichiers vides.

`core/apps.py` :

```python
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
```

`core/models.py` :

```python
"""Modèles métier du service de facturation.

dj-stripe fournit le miroir des objets Stripe (Customer, Price, Subscription,
Invoice…) : on ne le modifie jamais. Ces modèles-ci sont la couche au-dessus —
qui est le client, ce qu'il a acheté, et quel droit en découle.
"""
import secrets

from django.db import models


def generate_shared_secret() -> str:
    """Secret HMAC de 64 caractères pour la signature service-à-service (§8)."""
    return secrets.token_urlsafe(48)


class App(models.Model):
    """Une application de la flotte qui délègue sa facturation à ce service."""

    slug = models.SlugField(unique=True, help_text="poker, tm, pushit…")
    name = models.CharField(max_length=100)
    base_url = models.URLField(help_text="Racine de l'API de l'app, ex. https://poker-api.foxugly.com")
    entitlement_path = models.CharField(
        max_length=200,
        default="/api/billing/entitlement/",
        help_text="Chemin du endpoint qui reçoit les droits poussés.",
    )
    shared_secret = models.CharField(max_length=100, default=generate_shared_secret)
    active = models.BooleanField(default=True, help_text="Décoché : plus aucune livraison n'est émise.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("slug",)

    def __str__(self):
        return self.slug

    @property
    def entitlement_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/{self.entitlement_path.lstrip('/')}"


class Plan(models.Model):
    """Une offre d'une app, adossée à un ou deux prix Stripe."""

    MONTHLY = "monthly"
    YEARLY = "yearly"

    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="plans")
    code = models.CharField(max_length=50, help_text="team1, team5… l'identifiant utilisé par l'app")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price_monthly = models.ForeignKey(
        "djstripe.Price", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    price_yearly = models.ForeignKey(
        "djstripe.Price", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    quotas = models.JSONField(default=dict, help_text='Poussé tel quel à l\'app, ex. {"teams": 1}')
    sort_order = models.PositiveIntegerField(default=0)
    public = models.BooleanField(default=True, help_text="Décoché : masqué du catalogue de l'app.")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("app", "sort_order", "code")
        constraints = [models.UniqueConstraint(fields=("app", "code"), name="unique_plan_code_per_app")]

    def __str__(self):
        return f"{self.app.slug}/{self.code}"

    def price_for(self, interval: str):
        """Le Price Stripe pour un intervalle, ou None si non configuré."""
        return {self.MONTHLY: self.price_monthly, self.YEARLY: self.price_yearly}.get(interval)
```

- [ ] **Step 4 : Câbler l'app**

Dans `config/settings/base.py`, ajouter `"core.apps.CoreConfig",` à `INSTALLED_APPS`, juste après
`"accounts.apps.AccountsConfig",`.

- [ ] **Step 5 : Migration**

Run: `.venv/Scripts/python manage.py makemigrations core`
Expected: `core/migrations/0001_initial.py` — `App` et `Plan`.

- [ ] **Step 6 : Lancer les tests**

Run: `.venv/Scripts/pytest -q`
Expected: PASS — 17 passed.

- [ ] **Step 7 : Commit**

```bash
git add -A
git commit -m "feat(core): App and Plan, the fleet tenants and their Stripe-backed offers"
```

---

### Task 3 : Le pont et l'état — `AppCustomer`, `Entitlement`, `EntitlementDelivery`

**Files:**
- Modify: `core/models.py`
- Test: `core/tests/test_models_entitlement.py`

**Interfaces:**
- Consumes: `App`, `Plan` (Task 2), `djstripe.models.Customer`.
- Produces: `core.models.AppCustomer` (avec **`app` nullable** = client direct),
  `core.models.Entitlement` (`is_paid`, `status`, `plan_code`, `interval`, `quotas`,
  `current_period_end`, `grace_until`, `stripe_customer_id`, `source`, `computed_at`) et
  `core.models.EntitlementDelivery` (PK `UUIDField` = le `delivery_id`).
  `Entitlement.payload()` → `dict` prêt à être signé et poussé (lot L3).

- [ ] **Step 1 : Écrire les tests qui échouent**

`core/tests/test_models_entitlement.py` :

```python
import uuid

import pytest
from django.db import IntegrityError
from django.utils import timezone

from core.models import App, AppCustomer, Entitlement, EntitlementDelivery


@pytest.fixture
def app(db):
    return App.objects.create(slug="poker", name="Poker", base_url="https://poker-api.foxugly.com")


@pytest.mark.django_db
def test_app_customer_is_unique_per_app_and_external_user():
    AppCustomer.objects.create(app=None, email="direct@client.be")  # client consulting
    a = App.objects.create(slug="poker", name="Poker", base_url="https://a.foxugly.com")
    AppCustomer.objects.create(app=a, external_user_id="42", email="alice@x.be")

    with pytest.raises(IntegrityError):
        AppCustomer.objects.create(app=a, external_user_id="42", email="autre@x.be")


@pytest.mark.django_db
def test_app_customer_allows_a_direct_client_without_app():
    """app=NULL = prestation de consulting, hors flotte : aucun droit n'en découle (§16)."""
    customer = AppCustomer.objects.create(app=None, email="direct@client.be")

    assert customer.app is None
    assert customer.is_direct is True


@pytest.mark.django_db
def test_entitlement_payload_carries_everything_the_app_needs(app):
    ent = Entitlement.objects.create(
        app=app,
        external_user_id="42",
        is_paid=True,
        status="active",
        plan_code="team1",
        interval="monthly",
        quotas={"teams": 1},
        current_period_end=timezone.now(),
        stripe_customer_id="cus_123",
    )

    payload = ent.payload()

    assert payload["app"] == "poker"
    assert payload["external_user_id"] == "42"
    assert payload["is_paid"] is True
    assert payload["plan"] == "team1"
    assert payload["quotas"] == {"teams": 1}
    assert payload["stripe_customer_id"] == "cus_123"
    assert payload["grace_until"] is None
    assert "issued_at" in payload


@pytest.mark.django_db
def test_entitlement_is_unique_per_app_and_external_user(app):
    Entitlement.objects.create(app=app, external_user_id="42")

    with pytest.raises(IntegrityError):
        Entitlement.objects.create(app=app, external_user_id="42")


@pytest.mark.django_db
def test_delivery_id_is_a_uuid_primary_key(app):
    ent = Entitlement.objects.create(app=app, external_user_id="42")

    delivery = EntitlementDelivery.objects.create(entitlement=ent, payload=ent.payload())

    assert isinstance(delivery.pk, uuid.UUID)
    assert delivery.status == EntitlementDelivery.PENDING
    assert delivery.attempts == 0
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `.venv/Scripts/pytest core/tests/test_models_entitlement.py -q`
Expected: FAIL — `ImportError: cannot import name 'AppCustomer'`.

- [ ] **Step 3 : Ajouter les trois modèles**

À la fin de `core/models.py` :

```python
class AppCustomer(models.Model):
    """Le pont entre « l'utilisateur 42 de poker » et un Customer Stripe.

    `app=NULL` désigne un **client direct** (prestation de consulting, §16) : aucun
    Entitlement n'est calculé pour lui et aucune livraison n'est émise. La colonne est
    nullable dès la première migration — l'ajouter après coup imposerait une migration
    sur une table déjà peuplée en production.
    """

    app = models.ForeignKey(
        App, null=True, blank=True, on_delete=models.CASCADE, related_name="customers"
    )
    external_user_id = models.CharField(max_length=100, blank=True)
    email = models.EmailField()
    customer = models.ForeignKey(
        "djstripe.Customer", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("email",)
        constraints = [
            models.UniqueConstraint(
                fields=("app", "external_user_id"), name="unique_customer_per_app_user"
            )
        ]

    def __str__(self):
        return f"{self.app.slug if self.app else 'direct'}:{self.external_user_id or self.email}"

    @property
    def is_direct(self) -> bool:
        return self.app_id is None


class Entitlement(models.Model):
    """L'état de droit dérivé, par (app, utilisateur). C'est le seul objet poussé."""

    STRIPE = "stripe"
    MANUAL = "manual"
    BYPASS = "bypass"
    SOURCES = [(STRIPE, "Stripe"), (MANUAL, "Manuel"), (BYPASS, "Bypass")]

    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="entitlements")
    external_user_id = models.CharField(max_length=100)
    is_paid = models.BooleanField(default=False)
    status = models.CharField(max_length=32, blank=True, help_text="Statut Stripe brut")
    plan_code = models.CharField(max_length=50, blank=True)
    interval = models.CharField(max_length=16, blank=True)
    quotas = models.JSONField(default=dict)
    current_period_end = models.DateTimeField(null=True, blank=True)
    grace_until = models.DateTimeField(
        null=True, blank=True, help_text="Fin de la période de grâce après un échec de paiement (§6.6)"
    )
    stripe_customer_id = models.CharField(max_length=64, blank=True)
    source = models.CharField(max_length=16, choices=SOURCES, default=STRIPE)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("app", "external_user_id")
        constraints = [
            models.UniqueConstraint(
                fields=("app", "external_user_id"), name="unique_entitlement_per_app_user"
            )
        ]

    def __str__(self):
        return f"{self.app.slug}:{self.external_user_id} {'payé' if self.is_paid else 'non payé'}"

    def payload(self) -> dict:
        """Le corps exact poussé à l'application (§7). Figé à l'émission."""
        from django.utils import timezone

        return {
            "issued_at": timezone.now().isoformat(),
            "app": self.app.slug,
            "external_user_id": self.external_user_id,
            "is_paid": self.is_paid,
            "status": self.status,
            "plan": self.plan_code,
            "interval": self.interval,
            "quotas": self.quotas,
            "current_period_end": self.current_period_end.isoformat() if self.current_period_end else None,
            "grace_until": self.grace_until.isoformat() if self.grace_until else None,
            "stripe_customer_id": self.stripe_customer_id,
            "source": self.source,
        }


class EntitlementDelivery(models.Model):
    """Une tentative de livraison d'un droit à son application. Rejouable."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    STATUSES = [(PENDING, "En attente"), (DELIVERED, "Livré"), (FAILED, "Échec")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entitlement = models.ForeignKey(Entitlement, on_delete=models.CASCADE, related_name="deliveries")
    payload = models.JSONField()
    status = models.CharField(max_length=16, choices=STATUSES, default=PENDING)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name_plural = "entitlement deliveries"

    def __str__(self):
        return f"{self.id} {self.status}"
```

Ajouter en tête de `core/models.py` :

```python
import uuid
```

- [ ] **Step 4 : Migration et tests**

Run: `.venv/Scripts/python manage.py makemigrations core`
Run: `.venv/Scripts/pytest -q`
Expected: PASS — 22 passed.

⚠️ **Attendu à cette étape :** la contrainte `unique_customer_per_app_user` **ne bloque pas**
plusieurs clients directs, parce qu'en SQL `NULL != NULL`. C'est voulu : deux prestations de
consulting distinctes ne doivent pas se percuter.

- [ ] **Step 5 : Commit**

```bash
git add -A
git commit -m "feat(core): AppCustomer bridge, derived Entitlement and its delivery queue"
```

---

### Task 4 : `recompute_entitlement()` — le cœur du lot

**Files:**
- Create: `core/services.py`
- Test: `core/tests/test_recompute.py`

**Interfaces:**
- Consumes: tous les modèles des Tasks 2-3, `djstripe.models.Subscription`.
- Produces: `core.services.recompute_entitlement(app, external_user_id) -> Entitlement`,
  `core.services.period_end_of(subscription) -> datetime | None`, et la constante
  `PAID_STATUSES = {"active", "trialing"}`.

- [ ] **Step 1 : Écrire les tests qui échouent**

`core/tests/test_recompute.py` :

```python
from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import App, Entitlement, Plan
from core.services import period_end_of, recompute_entitlement


@pytest.fixture
def app(db):
    return App.objects.create(slug="poker", name="Poker", base_url="https://poker-api.foxugly.com")


@pytest.fixture
def plan(app):
    return Plan.objects.create(app=app, code="team1", name="1 équipe", quotas={"teams": 1})


def test_period_end_reads_the_subscription_item_first():
    """Stripe a déplacé current_period_end au niveau des items : le lire à la racine
    renvoie None en silence, et un abonnement payé paraît expiré (§14)."""
    end = 1790000000
    sub = {"items": {"data": [{"current_period_end": end}]}}

    assert period_end_of(sub) is not None


def test_period_end_falls_back_to_the_root_for_older_payloads():
    sub = {"current_period_end": 1790000000, "items": {"data": [{}]}}

    assert period_end_of(sub) is not None


def test_period_end_is_none_when_absent_everywhere():
    assert period_end_of({"items": {"data": []}}) is None


@pytest.mark.django_db
def test_without_any_subscription_the_user_is_not_paid(app):
    ent = recompute_entitlement(app, "42")

    assert ent.is_paid is False
    assert ent.plan_code == ""
    assert ent.quotas == {}


@pytest.mark.django_db
def test_manual_source_is_never_overwritten_by_a_recompute(app):
    """Un accès offert depuis la console ne doit pas être effacé par un webhook Stripe."""
    Entitlement.objects.create(
        app=app, external_user_id="42", is_paid=True, source=Entitlement.MANUAL, quotas={"teams": 9}
    )

    ent = recompute_entitlement(app, "42")

    assert ent.source == Entitlement.MANUAL
    assert ent.is_paid is True
    assert ent.quotas == {"teams": 9}


@pytest.mark.django_db
def test_grace_period_keeps_access_open_after_a_failed_payment(app, plan, settings):
    settings.BILLING_GRACE_DAYS = 7
    ent = Entitlement.objects.create(
        app=app, external_user_id="42", is_paid=True, status="active", plan_code="team1"
    )

    ent = recompute_entitlement(app, "42", stripe_status="past_due", plan=plan, interval="monthly")

    assert ent.status == "past_due"
    assert ent.is_paid is True, "l'accès reste ouvert pendant la grâce"
    assert ent.grace_until is not None
    assert ent.grace_until > timezone.now()


@pytest.mark.django_db
def test_access_closes_once_the_grace_period_has_expired(app, plan, settings):
    settings.BILLING_GRACE_DAYS = 7
    Entitlement.objects.create(
        app=app,
        external_user_id="42",
        is_paid=True,
        status="past_due",
        grace_until=timezone.now() - timedelta(days=1),
    )

    ent = recompute_entitlement(app, "42", stripe_status="past_due", plan=plan, interval="monthly")

    assert ent.is_paid is False


@pytest.mark.django_db
def test_grace_days_zero_closes_access_at_the_first_failure(app, plan, settings):
    settings.BILLING_GRACE_DAYS = 0

    ent = recompute_entitlement(app, "42", stripe_status="past_due", plan=plan, interval="monthly")

    assert ent.is_paid is False


@pytest.mark.django_db
def test_a_canceled_subscription_closes_access_even_within_the_grace_window(app, plan, settings):
    settings.BILLING_GRACE_DAYS = 7

    ent = recompute_entitlement(app, "42", stripe_status="canceled", plan=plan, interval="monthly")

    assert ent.is_paid is False


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["active", "trialing"])
def test_active_and_trialing_grant_the_plan_quotas(app, plan, status):
    ent = recompute_entitlement(app, "42", stripe_status=status, plan=plan, interval="monthly")

    assert ent.is_paid is True
    assert ent.plan_code == "team1"
    assert ent.interval == "monthly"
    assert ent.quotas == {"teams": 1}
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `.venv/Scripts/pytest core/tests/test_recompute.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.services'`.

- [ ] **Step 3 : Ajouter le réglage de grâce**

Dans `config/settings/base.py`, après le bloc Stripe :

```python
# Jours de grâce accordés après un échec de paiement avant de fermer l'accès (§6.6).
# 0 désactive la grâce. Réglable en SSM sans redéploiement.
BILLING_GRACE_DAYS = env.int("BILLING_GRACE_DAYS", default=7)
```

- [ ] **Step 4 : Implémenter**

`core/services.py` :

```python
"""Dérivation des droits depuis l'état Stripe.

C'est ici que « un abonnement Stripe » devient « cet utilisateur a le droit de… ».
Aucun appel réseau : la fonction lit le miroir dj-stripe et nos modèles.
"""
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from .models import Entitlement

# Les seuls statuts Stripe qui ouvrent l'accès sans condition.
PAID_STATUSES = {"active", "trialing"}
# Statuts qui ferment l'accès immédiatement, même pendant une grâce en cours.
TERMINAL_STATUSES = {"canceled", "unpaid", "incomplete_expired"}


def period_end_of(subscription: dict):
    """Fin de période d'un abonnement Stripe, en datetime aware.

    Les versions récentes de l'API portent `current_period_end` sur les **items**
    et non plus à la racine. Lire la racine seule renvoie None en silence, et un
    abonnement parfaitement payé paraît alors expiré — d'où le repli explicite.
    """
    epoch = None
    try:
        epoch = subscription["items"]["data"][0].get("current_period_end")
    except (KeyError, IndexError, TypeError):
        epoch = None
    if not epoch:
        epoch = subscription.get("current_period_end") if isinstance(subscription, dict) else None
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.get_current_timezone())


def recompute_entitlement(app, external_user_id, *, stripe_status="", plan=None, interval="",
                          period_end=None, stripe_customer_id=""):
    """Recalcule et persiste le droit d'un utilisateur. Renvoie l'Entitlement.

    `source=manual` (accès offert depuis la console) est **préservé** : un webhook
    Stripe ne doit pas révoquer une décision humaine.
    """
    ent, _ = Entitlement.objects.get_or_create(app=app, external_user_id=external_user_id)

    if ent.source == Entitlement.MANUAL:
        return ent

    now = timezone.now()
    grace_days = getattr(settings, "BILLING_GRACE_DAYS", 7)

    if stripe_status in PAID_STATUSES:
        is_paid, grace_until = True, None
    elif stripe_status in TERMINAL_STATUSES or not stripe_status:
        is_paid, grace_until = False, None
    else:
        # past_due, incomplete… : on laisse Stripe relancer la carte pendant N jours.
        grace_until = ent.grace_until or (now + timedelta(days=grace_days))
        is_paid = grace_days > 0 and grace_until > now

    ent.status = stripe_status
    ent.is_paid = is_paid
    ent.grace_until = grace_until
    ent.plan_code = plan.code if plan else ""
    ent.interval = interval if plan else ""
    ent.quotas = plan.quotas if (plan and is_paid) else {}
    ent.current_period_end = period_end
    if stripe_customer_id:
        ent.stripe_customer_id = stripe_customer_id
    ent.source = Entitlement.STRIPE
    ent.save()
    return ent
```

- [ ] **Step 5 : Lancer les tests**

Run: `.venv/Scripts/pytest -q`
Expected: PASS — 34 passed.

- [ ] **Step 6 : Commit**

```bash
git add -A
git commit -m "feat(core): derive entitlements from Stripe state, with a grace window"
```

---

### Task 5 : Admin Django

**Files:**
- Create: `core/admin.py`
- Test: `core/tests/test_admin.py`

**Interfaces:**
- Consumes: les modèles des Tasks 2-3, `accounts.User` (lot L1) pour le login staff.
- Produces: les cinq modèles administrables. `EntitlementDelivery` et `Entitlement` sont en
  **lecture seule** sur les champs dérivés — on ne bricole pas un droit à la main sans passer
  par `source=manual`.

- [ ] **Step 1 : Écrire le test qui échoue**

`core/tests/test_admin.py` :

```python
import pytest
from django.contrib.admin.sites import site

from core.models import App, AppCustomer, Entitlement, EntitlementDelivery, Plan


@pytest.mark.parametrize("model", [App, Plan, AppCustomer, Entitlement, EntitlementDelivery])
def test_every_business_model_is_registered_in_the_admin(model):
    assert model in site._registry


@pytest.mark.django_db
def test_admin_index_is_reachable_by_a_staff_user(client, django_user_model):
    user = django_user_model.objects.create_superuser(email="ops@foxugly.com", password="x")
    client.force_login(user)

    assert client.get("/admin/").status_code == 200
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `.venv/Scripts/pytest core/tests/test_admin.py -q`
Expected: FAIL — les modèles ne sont pas enregistrés.

- [ ] **Step 3 : Implémenter**

`core/admin.py` :

```python
from django.contrib import admin

from .models import App, AppCustomer, Entitlement, EntitlementDelivery, Plan


class PlanInline(admin.TabularInline):
    model = Plan
    extra = 0
    fields = ("code", "name", "price_monthly", "price_yearly", "quotas", "sort_order", "public", "active")


@admin.register(App)
class AppAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "base_url", "active")
    list_filter = ("active",)
    search_fields = ("slug", "name")
    inlines = [PlanInline]
    # Le secret HMAC ne se modifie pas à la main : sa rotation est un geste dédié (lot L3).
    readonly_fields = ("shared_secret", "created_at")


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("app", "code", "name", "sort_order", "public", "active")
    list_filter = ("app", "public", "active")
    search_fields = ("code", "name")


@admin.register(AppCustomer)
class AppCustomerAdmin(admin.ModelAdmin):
    list_display = ("email", "app", "external_user_id", "customer", "created_at")
    list_filter = ("app",)
    search_fields = ("email", "external_user_id")
    readonly_fields = ("created_at",)


@admin.register(Entitlement)
class EntitlementAdmin(admin.ModelAdmin):
    list_display = ("app", "external_user_id", "is_paid", "status", "plan_code", "source", "computed_at")
    list_filter = ("app", "is_paid", "source", "status")
    search_fields = ("external_user_id",)
    # Dérivés d'un recalcul : les éditer à la main créerait un état que le prochain
    # webhook écraserait sans prévenir. Offrir un accès = passer source à "manual".
    readonly_fields = ("status", "current_period_end", "grace_until", "computed_at")


@admin.register(EntitlementDelivery)
class EntitlementDeliveryAdmin(admin.ModelAdmin):
    list_display = ("id", "entitlement", "status", "attempts", "next_retry_at", "delivered_at")
    list_filter = ("status",)
    readonly_fields = ("id", "entitlement", "payload", "attempts", "last_error", "delivered_at", "created_at")
```

- [ ] **Step 4 : Lancer les tests**

Run: `.venv/Scripts/pytest -q`
Expected: PASS — 41 passed.

- [ ] **Step 5 : Commit**

```bash
git add -A
git commit -m "feat(core): Django admin for the five business models"
```

---

### Task 6 : Brancher les webhooks Stripe

**Files:**
- Create: `core/webhooks.py`
- Modify: `core/apps.py` (import du module dans `ready()`)
- Test: `core/tests/test_webhooks.py`

**Interfaces:**
- Consumes: `recompute_entitlement` (Task 4), `djstripe.event_handlers`.
- Produces: les handlers dj-stripe pour `checkout.session.completed`,
  `customer.subscription.*`, `invoice.paid`, `invoice.payment_failed`, et la fonction
  `resolve_target(event_data) -> (App, external_user_id) | (None, None)`.

- [ ] **Step 1 : Écrire les tests qui échouent**

`core/tests/test_webhooks.py` :

```python
import pytest

from core.models import App
from core.webhooks import resolve_target


@pytest.fixture
def app(db):
    return App.objects.create(slug="poker", name="Poker", base_url="https://poker-api.foxugly.com")


@pytest.mark.django_db
def test_resolve_target_reads_the_metadata(app):
    obj = {"metadata": {"app": "poker", "external_user_id": "42"}}

    assert resolve_target(obj) == (app, "42")


@pytest.mark.django_db
def test_resolve_target_falls_back_to_client_reference_id(app):
    obj = {"metadata": {}, "client_reference_id": "poker:42"}

    assert resolve_target(obj) == (app, "42")


@pytest.mark.django_db
def test_resolve_target_returns_none_for_an_unknown_app():
    obj = {"metadata": {"app": "inconnue", "external_user_id": "42"}}

    assert resolve_target(obj) == (None, None)


@pytest.mark.django_db
def test_resolve_target_returns_none_when_nothing_identifies_the_user(app):
    assert resolve_target({"metadata": {}}) == (None, None)


@pytest.mark.django_db
def test_resolve_target_ignores_a_malformed_client_reference_id(app):
    assert resolve_target({"client_reference_id": "n-importe-quoi"}) == (None, None)
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `.venv/Scripts/pytest core/tests/test_webhooks.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.webhooks'`.

- [ ] **Step 3 : Implémenter**

`core/webhooks.py` :

```python
"""Réception des événements Stripe.

dj-stripe vérifie la signature Stripe, persiste l'événement et met à jour son
miroir avant de nous appeler : l'idempotence sur `event.id` est donc déjà acquise
et un renvoi de Stripe ne retraite rien.

Ce module ne fait qu'une chose : identifier de qui parle l'événement, puis
demander un recalcul. La livraison à l'application arrive au lot L3.
"""
import logging

from djstripe import event_handlers  # noqa: F401  (importé pour son effet de bord)
from djstripe.models import Event

from .models import App
from .services import period_end_of, recompute_entitlement

logger = logging.getLogger("billing")


def resolve_target(obj: dict):
    """(App, external_user_id) depuis les metadata, ou (None, None).

    Deux chemins : `metadata` posé au checkout, et `client_reference_id` au format
    "<app>:<user>" en secours — Stripe ne propage pas toujours les metadata sur
    tous les objets d'une même session.
    """
    metadata = obj.get("metadata") or {}
    slug, user_id = metadata.get("app"), metadata.get("external_user_id")

    if not (slug and user_id):
        reference = obj.get("client_reference_id") or ""
        if ":" in reference:
            slug, user_id = reference.split(":", 1)

    if not (slug and user_id):
        return None, None

    app = App.objects.filter(slug=slug).first()
    return (app, user_id) if app else (None, None)
```

- [ ] **Step 4 : Lancer les tests**

Run: `.venv/Scripts/pytest -q`
Expected: PASS — 46 passed.

- [ ] **Step 5 : Commit et PR**

```bash
git add -A
git commit -m "feat(core): resolve the Stripe event target from metadata"
git push -u origin feat/l2-djstripe-models
gh pr create --fill --base main
```

---

## Critère de sortie du lot L2

1. `pytest -q` vert, ~46 tests, **aucun appel réseau**
2. `manage.py makemigrations --check --dry-run` sort en 0
3. La CI de la PR est verte (job `test`) — **ne pas merger** avant relecture
4. L'admin Django expose les cinq modèles plus ceux de dj-stripe
5. `manage.py migrate` s'applique proprement sur une base vierge **et** sur la base de prod

## Ce que ce lot ne fait PAS

- Aucune livraison vers les applications : `EntitlementDelivery` est créé mais rien ne le
  consomme (lot L3, avec Celery et Redis db4).
- Aucune API service-à-service, aucune signature HMAC (lot L3).
- Aucune clé Stripe en SSM, aucun endpoint webhook enregistré côté Stripe (lot L6).
