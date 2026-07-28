import pytest

from core.models import App, EntitlementDelivery, Plan
from core.webhooks import handle_subscription_event, price_id_of, resolve_target


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
    assert resolve_target({}) == (None, None)
    assert resolve_target(None) == (None, None)


@pytest.mark.django_db
def test_resolve_target_ignores_a_malformed_client_reference_id(app):
    assert resolve_target({"client_reference_id": "n-importe-quoi"}) == (None, None)


def test_price_id_of_reads_the_first_item():
    assert price_id_of({"items": {"data": [{"price": {"id": "price_123"}}]}}) == "price_123"
    assert price_id_of({"items": {"data": []}}) == ""
    assert price_id_of({}) == ""


@pytest.mark.django_db
def test_an_event_for_an_unknown_app_is_ignored_without_error():
    """Un compte Stripe partagé reçoit aussi des événements qui ne nous regardent pas."""
    result = handle_subscription_event({"metadata": {"app": "inconnue", "external_user_id": "1"}})

    assert result is None
    assert EntitlementDelivery.objects.count() == 0


@pytest.mark.django_db
def test_a_subscription_event_recomputes_and_queues_a_delivery(app):
    obj = {
        "metadata": {"app": "poker", "external_user_id": "42"},
        "status": "active",
        "customer": "cus_123",
        "items": {"data": [{"current_period_end": 1790000000}]},
    }

    delivery = handle_subscription_event(obj)

    assert delivery is not None
    assert delivery.status == EntitlementDelivery.PENDING
    assert delivery.payload["is_paid"] is True
    assert delivery.payload["stripe_customer_id"] == "cus_123"
    assert delivery.payload["current_period_end"] is not None


@pytest.mark.django_db
def test_an_unknown_price_still_grants_access_but_without_quotas(app):
    """Un prix hors catalogue ne doit pas faire échouer le webhook — mais on ne peut
    pas inventer de quotas, donc l'app n'en reçoit aucun."""
    obj = {
        "metadata": {"app": "poker", "external_user_id": "42"},
        "status": "active",
        "items": {"data": [{"price": {"id": "price_inconnu"}}]},
    }

    delivery = handle_subscription_event(obj)

    assert delivery.payload["is_paid"] is True
    assert delivery.payload["plan"] == ""
    assert delivery.payload["quotas"] == {}


@pytest.mark.django_db
def test_a_canceled_subscription_queues_a_delivery_that_closes_access(app):
    Plan.objects.create(app=app, code="team1", name="1 équipe", quotas={"teams": 1})
    obj = {"metadata": {"app": "poker", "external_user_id": "42"}, "status": "canceled"}

    delivery = handle_subscription_event(obj)

    assert delivery.payload["is_paid"] is False
    assert delivery.payload["quotas"] == {}


@pytest.mark.django_db
def test_the_receiver_is_actually_connected_to_the_djstripe_signal(app):
    """Prouve le câblage, pas seulement la fonction : on émet le vrai signal que
    dj-stripe déclenche après avoir validé la signature Stripe."""
    from djstripe.models import Event
    from djstripe.signals import WEBHOOK_SIGNALS

    event = Event(
        id="evt_test",
        type="customer.subscription.updated",
        data={
            "object": {
                "metadata": {"app": "poker", "external_user_id": "42"},
                "status": "active",
                "customer": "cus_123",
            }
        },
    )

    WEBHOOK_SIGNALS["customer.subscription.updated"].send(sender=Event, event=event)

    assert EntitlementDelivery.objects.count() == 1
    assert EntitlementDelivery.objects.get().payload["is_paid"] is True
