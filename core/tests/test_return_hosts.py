"""Hôtes de retour autorisés, déclarés par app (audit 2026-10-18, M2).

Avant : toute URL sur le domaine parent de `base_url` passait, donc poker pouvait
renvoyer un payeur vers pushit, billing ou un sous-domaine pendouillant. Désormais
chaque app déclare ses hôtes ; sans déclaration, checkout et portail refusent.
"""
import importlib
from unittest.mock import MagicMock, patch

import pytest
from django.apps import apps as django_apps

from core.models import App, Entitlement, Plan

BODY = {
    "external_user_id": "42",
    "plan": "team1",
    "interval": "monthly",
    "success_url": "https://poker.foxugly.invalid/ok",
    "cancel_url": "https://poker.foxugly.invalid/ko",
}


@pytest.fixture
def stripe_ready(app, settings):
    settings.STRIPE_TEST_SECRET_KEY = "sk_test_factice"
    Plan.objects.create(app=app, code="team1", name="1 équipe")
    stripe = MagicMock()
    stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/c/x")
    stripe.billing_portal.Session.create.return_value = MagicMock(url="https://billing.stripe.com/p/x")
    with patch.object(Plan, "price_for", return_value=MagicMock(id="price_x")), patch(
        "core.api_views.stripe_client", return_value=stripe
    ), patch("core.api_views.trial_days_for", return_value=0):
        yield stripe


# ------------------------------------------------------------------ API signée

@pytest.mark.django_db
def test_checkout_accepts_a_declared_host(app, stripe_ready, signed_post):
    response = signed_post("/api/v1/checkout/", BODY, app)

    assert response.status_code == 200


@pytest.mark.django_db
def test_checkout_refuses_another_app_host(app, stripe_ready, signed_post):
    body = {**BODY, "success_url": "https://pushit.foxugly.invalid/billing"}

    response = signed_post("/api/v1/checkout/", body, app)

    assert response.status_code == 400
    assert response.json()["code"] == "bad_return_url"
    stripe_ready.checkout.Session.create.assert_not_called()


@pytest.mark.django_db
def test_checkout_refuses_when_the_app_declares_no_host(app, stripe_ready, signed_post):
    app.return_hosts = []
    app.save()

    response = signed_post("/api/v1/checkout/", BODY, app)

    assert response.status_code == 400
    assert response.json()["code"] == "return_hosts_unconfigured"
    stripe_ready.checkout.Session.create.assert_not_called()


@pytest.mark.django_db
def test_portal_refuses_when_the_app_declares_no_host(app, stripe_ready, signed_post):
    app.return_hosts = []
    app.save()
    Entitlement.objects.create(app=app, external_user_id="42", stripe_customer_id="cus_42")

    response = signed_post(
        "/api/v1/portal/",
        {"external_user_id": "42", "return_url": "https://poker.foxugly.invalid/teams"},
        app,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "return_hosts_unconfigured"
    stripe_ready.billing_portal.Session.create.assert_not_called()


@pytest.mark.django_db
def test_portal_refuses_another_app_host(app, stripe_ready, signed_post):
    Entitlement.objects.create(app=app, external_user_id="42", stripe_customer_id="cus_42")

    response = signed_post(
        "/api/v1/portal/",
        {"external_user_id": "42", "return_url": "https://pushit.foxugly.invalid/billing"},
        app,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "bad_return_url"


# ------------------------------------------------------------------ console

@pytest.fixture
def staff_client(db, django_user_model):
    from rest_framework.test import APIClient

    c = APIClient()
    c.force_authenticate(django_user_model.objects.create_superuser(email="ops@example.com", password="x"))
    return c


@pytest.mark.django_db
def test_the_console_exposes_and_updates_return_hosts(app, staff_client):
    assert staff_client.get(f"/api/v1/admin/apps/{app.pk}/").json()["return_hosts"] == [
        "poker.foxugly.invalid"
    ]

    response = staff_client.patch(
        f"/api/v1/admin/apps/{app.pk}/",
        {"return_hosts": ["Poker.foxugly.invalid", "www.poker.foxugly.invalid"]},
        format="json",
    )

    assert response.status_code == 200, response.content
    app.refresh_from_db()
    # Normalisés en minuscules : la comparaison se fait sur le hostname urlparse.
    assert app.return_hosts == ["poker.foxugly.invalid", "www.poker.foxugly.invalid"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad",
    [
        "https://poker.foxugly.invalid",  # une URL, pas un hôte
        "poker.foxugly.invalid/teams",
        "*.foxugly.invalid",  # pas de joker
        "attaquant.example",  # hors flotte
        "poker.foxugly.invalid:8443",
        "",
    ],
)
def test_the_console_rejects_a_malformed_or_foreign_host(app, staff_client, bad):
    response = staff_client.patch(
        f"/api/v1/admin/apps/{app.pk}/", {"return_hosts": [bad]}, format="json"
    )

    assert response.status_code == 400
    app.refresh_from_db()
    assert app.return_hosts == ["poker.foxugly.invalid"]


# ------------------------------------------------------------------ migration

def _seed():
    return importlib.import_module("core.migrations.0007_app_return_hosts").seed_return_hosts


@pytest.mark.django_db
def test_the_data_migration_seeds_the_spa_host_from_the_api_host():
    """Les apps de prod (2026-10-18) renvoient toutes vers `<x>.foxugly.com`,
    le frère de leur API `<x>-api.foxugly.com` (leur FRONTEND_BASE_URL)."""
    poker = App.objects.create(slug="poker", name="Poker", base_url="https://poker-api.foxugly.com")
    facil = App.objects.create(
        slug="facilitation1", name="Facilitation", base_url="https://facilitation-api.foxugly.com/"
    )
    odd = App.objects.create(slug="odd", name="Sans -api", base_url="https://odd.foxugly.com")
    done = App.objects.create(
        slug="done", name="Déjà réglée", base_url="https://done-api.foxugly.com", return_hosts=["x.foxugly.com"]
    )

    _seed()(django_apps, None)

    for obj in (poker, facil, odd, done):
        obj.refresh_from_db()
    assert poker.return_hosts == ["poker.foxugly.com"]
    assert facil.return_hosts == ["facilitation.foxugly.com"]
    # Pas de frère devinable : rien n'est semé, l'app recevra 400 jusqu'à
    # configuration explicite dans la console.
    assert odd.return_hosts == []
    assert done.return_hosts == ["x.foxugly.com"]
