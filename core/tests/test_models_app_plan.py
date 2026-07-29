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
    """Le défaut doit être le chemin canonique de la flotte, pas l'ancien.

    Une app créée avec le chemin legacy `/api/billing/entitlement/` échoue en
    silence : l'alias transitoire ne couvre pas les endpoints signés (la
    signature HMAC porte le chemin, OPERATIONS.md §3.18), donc chaque push est
    refusé et rien ne le signale côté central.
    """
    app = App.objects.create(slug="poker", name="Poker", base_url="https://poker-api.foxugly.com")

    assert app.entitlement_path == "/api/v1/billing/entitlement/"
    assert app.entitlement_url == "https://poker-api.foxugly.com/api/v1/billing/entitlement/"
    assert app.active is True


@pytest.mark.django_db
def test_app_generates_a_distinct_shared_secret_per_app():
    a = App.objects.create(slug="poker", name="Poker", base_url="https://a.foxugly.com")
    b = App.objects.create(slug="tm", name="TM", base_url="https://b.foxugly.com")

    assert a.shared_secret and b.shared_secret
    assert a.shared_secret != b.shared_secret


@pytest.mark.django_db
def test_app_entitlement_url_joins_base_and_path():
    """Une seule barre oblique à la jointure, quel que soit le chemin configuré."""
    app = App.objects.create(
        slug="poker",
        name="Poker",
        base_url="https://poker-api.foxugly.com/",
        entitlement_path="/sur-mesure/droits/",
    )

    assert app.entitlement_url == "https://poker-api.foxugly.com/sur-mesure/droits/"


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
