"""Facturation à l'unité : « 2 € par application » suppose que le quota suive la
quantité souscrite. Sans ça, payer pour cinq applications n'en débloquerait qu'une.
"""
from unittest.mock import MagicMock, patch

import pytest

from core.models import App, Entitlement, Plan
from core.services import recompute_entitlement
from core.webhooks import handle_subscription_event, quantity_of


@pytest.fixture
def per_unit(app):
    """Un plan facturé à l'unité : le quota d'applications suit la quantité."""
    return Plan.objects.create(
        app=app, code="app", name="Par application",
        quotas={}, per_unit_quota_key="applications",
    )


@pytest.fixture
def flat(app):
    """Un plan forfaitaire : illimité reste illimité, quelle que soit la quantité."""
    return Plan.objects.create(
        app=app, code="unlimited", name="Illimité", quotas={"applications": 10000},
    )


# ------------------------------------------------------------------ le modèle

@pytest.mark.django_db
def test_a_per_unit_plan_follows_the_subscribed_quantity(per_unit):
    assert per_unit.quotas_for(1) == {"applications": 1}
    assert per_unit.quotas_for(5) == {"applications": 5}
    assert per_unit.quotas_for(37) == {"applications": 37}


@pytest.mark.django_db
def test_a_flat_plan_ignores_the_quantity(flat):
    """Souscrire deux fois l'illimité ne double pas l'illimité."""
    assert flat.quotas_for(1) == {"applications": 10000}
    assert flat.quotas_for(3) == {"applications": 10000}


@pytest.mark.django_db
def test_a_quantity_below_one_never_yields_a_zero_quota(per_unit):
    """Un 0 ou un None ferme l'accès d'un client qui paie : on plancher à 1."""
    assert per_unit.quotas_for(0) == {"applications": 1}
    assert per_unit.quotas_for(None) == {"applications": 1}


# --------------------------------------------------------------- le recalcul

@pytest.mark.django_db
def test_recompute_grants_the_quantity_as_quota(app, per_unit):
    ent = recompute_entitlement(
        app, "42", stripe_status="active", plan=per_unit, interval="monthly", quantity=4
    )

    assert ent.is_paid is True
    assert ent.quotas == {"applications": 4}


@pytest.mark.django_db
def test_recompute_defaults_to_one_when_no_quantity_is_given(app, per_unit):
    ent = recompute_entitlement(app, "42", stripe_status="active", plan=per_unit, interval="monthly")

    assert ent.quotas == {"applications": 1}


@pytest.mark.django_db
def test_an_unpaid_entitlement_grants_no_quota_whatever_the_quantity(app, per_unit):
    ent = recompute_entitlement(
        app, "42", stripe_status="canceled", plan=per_unit, interval="monthly", quantity=9
    )

    assert ent.is_paid is False
    assert ent.quotas == {}


# ---------------------------------------------------------------- le webhook

def test_quantity_is_read_from_the_subscription_item():
    assert quantity_of({"items": {"data": [{"quantity": 6}]}}) == 6


def test_quantity_falls_back_to_one_when_absent():
    """Un abonnement forfaitaire ne porte pas toujours de quantité."""
    assert quantity_of({"items": {"data": [{}]}}) == 1
    assert quantity_of({"items": {"data": []}}) == 1
    assert quantity_of({}) == 1


@pytest.mark.django_db
def test_a_subscription_event_carries_the_quantity_through(app, per_unit):
    price = MagicMock(id="price_unit")
    with patch.object(Plan, "price_for", return_value=price), patch(
        "core.tasks.deliver_entitlement.delay"
    ):
        Plan.objects.filter(pk=per_unit.pk).update(price_monthly=None)
        with patch("core.webhooks.plan_for_price", return_value=(per_unit, "monthly")):
            delivery = handle_subscription_event(
                {
                    "metadata": {"app": "poker", "external_user_id": "42"},
                    "status": "active",
                    "items": {"data": [{"quantity": 3, "price": {"id": "price_unit"}}]},
                }
            )

    assert delivery.payload["quotas"] == {"applications": 3}


@pytest.mark.django_db
def test_reducing_the_quantity_reduces_the_quota(app, per_unit):
    """Passer de 5 à 2 applications doit refermer l'accès aux 3 en trop."""
    recompute_entitlement(app, "42", stripe_status="active", plan=per_unit, interval="monthly", quantity=5)

    ent = recompute_entitlement(
        app, "42", stripe_status="active", plan=per_unit, interval="monthly", quantity=2
    )

    assert ent.quotas == {"applications": 2}
    assert Entitlement.objects.get(app=app, external_user_id="42").quotas == {"applications": 2}
