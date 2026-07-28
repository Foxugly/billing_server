import uuid

import pytest
from django.db import IntegrityError
from django.utils import timezone

from core.models import App, AppCustomer, Entitlement, EntitlementDelivery


@pytest.fixture
def app(db):
    return App.objects.create(slug="poker", name="Poker", base_url="https://poker-api.foxugly.com")


@pytest.mark.django_db
def test_app_customer_is_unique_per_app_and_external_user(app):
    AppCustomer.objects.create(app=app, external_user_id="42", email="alice@x.be")

    with pytest.raises(IntegrityError):
        AppCustomer.objects.create(app=app, external_user_id="42", email="autre@x.be")


@pytest.mark.django_db
def test_app_customer_allows_a_direct_client_without_app():
    """app=NULL = prestation de consulting, hors flotte : aucun droit n'en découle (§16)."""
    customer = AppCustomer.objects.create(app=None, email="direct@client.be")

    assert customer.app is None
    assert customer.is_direct is True


@pytest.mark.django_db
def test_several_direct_clients_may_coexist():
    """En SQL, NULL != NULL : la contrainte d'unicité ne les oppose pas. C'est voulu."""
    AppCustomer.objects.create(app=None, email="premier@client.be")
    AppCustomer.objects.create(app=None, email="second@client.be")

    assert AppCustomer.objects.filter(app__isnull=True).count() == 2


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
