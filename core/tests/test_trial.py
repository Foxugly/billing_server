"""Essai gratuit : premier mois offert, une seule fois par client, quantité 1.

Un essai mal borné se paie deux fois — en service rendu gratuitement, et en
clients qui apprennent à résilier pour repartir à zéro.
"""
from unittest.mock import MagicMock

import pytest

from core.models import AppCustomer, Plan
from core.stripe_gateway import trial_days_for


@pytest.fixture
def plan_essai(app):
    return Plan.objects.create(
        app=app, code="app", name="Par application",
        per_unit_quota_key="applications", trial_days=30,
    )


@pytest.fixture
def plan_sans_essai(app):
    return Plan.objects.create(app=app, code="unlimited", name="Illimité", trial_days=0)


def _stripe(avec_anterieurs=False, echoue=False):
    s = MagicMock()
    if echoue:
        s.Subscription.list.side_effect = RuntimeError("stripe indisponible")
    else:
        s.Subscription.list.return_value = {"data": [{"id": "sub_1"}] if avec_anterieurs else []}
    return s


@pytest.mark.django_db
def test_a_brand_new_customer_gets_the_trial(app, plan_essai):
    """Aucun client Stripe encore : c'est forcément un premier abonnement."""
    assert trial_days_for(_stripe(), plan_essai, None, 1) == 30


@pytest.mark.django_db
def test_a_customer_without_any_past_subscription_gets_the_trial(app, plan_essai):
    client = AppCustomer.objects.create(app=app, external_user_id="42", email="a@example.com")

    assert trial_days_for(_stripe(avec_anterieurs=False), plan_essai, client, 1) == 30


@pytest.mark.django_db
def test_a_customer_who_already_subscribed_gets_no_trial(app, plan_essai):
    """Sinon il suffirait de résilier puis se réabonner pour être gratuit à vie."""
    client = AppCustomer.objects.create(app=app, external_user_id="42", email="a@example.com")
    client.customer_id = "cus_123"

    assert trial_days_for(_stripe(avec_anterieurs=True), plan_essai, client, 1) == 0


@pytest.mark.django_db
def test_no_trial_beyond_a_single_unit(app, plan_essai):
    """Sinon on offrirait cinquante applications pendant un mois."""
    assert trial_days_for(_stripe(), plan_essai, None, 2) == 0
    assert trial_days_for(_stripe(), plan_essai, None, 50) == 0


@pytest.mark.django_db
def test_a_plan_without_trial_never_grants_one(app, plan_sans_essai):
    assert trial_days_for(_stripe(), plan_sans_essai, None, 1) == 0


@pytest.mark.django_db
def test_when_stripe_cannot_be_reached_no_trial_is_granted(app, plan_essai):
    """En cas de doute on refuse : un essai refusé à tort se rattrape à la main,
    un essai offert en boucle ne se rattrape pas."""
    client = AppCustomer.objects.create(app=app, external_user_id="42", email="a@example.com")
    client.customer_id = "cus_123"

    assert trial_days_for(_stripe(echoue=True), plan_essai, client, 1) == 0


@pytest.mark.django_db
def test_a_trialing_subscription_grants_access(app, plan_essai):
    """`trialing` doit ouvrir l'accès : sinon l'essai ne servirait à rien."""
    from core.services import PAID_STATUSES, recompute_entitlement

    assert "trialing" in PAID_STATUSES
    ent = recompute_entitlement(
        app, "42", stripe_status="trialing", plan=plan_essai, interval="monthly", quantity=1
    )

    assert ent.is_paid is True
    assert ent.quotas == {"applications": 1}
