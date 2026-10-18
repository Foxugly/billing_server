"""Rattachement d'un événement Stripe à un utilisateur, et ordre des événements.

Audit 2026-10-18 :
- B2 : les metadata étaient posées sur la session Checkout, jamais sur
  l'abonnement. Un objet Subscription réel n'a ni ces metadata ni
  `client_reference_id` : il faut les poser via `subscription_data.metadata`, et
  retrouver les abonnements existants par leur client Stripe.
- I4 : Stripe ne garantit pas l'ordre de livraison. Un `updated(active)` tardif
  ne doit pas rouvrir un accès résilié.

Les fixtures partent de la forme RÉELLE d'un objet Subscription (champs racine
de l'API 2025+, période sur les items) — c'est l'angle mort qui avait laissé
passer B2.
"""
import copy
from datetime import datetime
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

import pytest

from core.models import AppCustomer, Entitlement, EntitlementDelivery, Plan
from core.webhooks import (
    handle_checkout_completed,
    handle_subscription_event,
    resolve_target,
)


@pytest.fixture(autouse=True)
def ne_pas_livrer():
    with patch("core.tasks.deliver_entitlement.delay") as delay:
        yield delay


# Un abonnement tel que Stripe l'envoie après un Checkout SANS subscription_data.metadata.
REAL_SUBSCRIPTION = {
    "id": "sub_1Q",
    "object": "subscription",
    "cancel_at_period_end": False,
    "canceled_at": None,
    "created": 1790000000,
    "customer": "cus_REAL",
    "items": {
        "object": "list",
        "data": [
            {
                "id": "si_1",
                "object": "subscription_item",
                "current_period_end": 1792592000,
                "current_period_start": 1790000000,
                "price": {"id": "price_poker_monthly", "object": "price", "recurring": {"interval": "month"}},
                "quantity": 1,
            }
        ],
    },
    "latest_invoice": "in_1",
    "livemode": True,
    "metadata": {},
    "start_date": 1790000000,
    "status": "active",
}


def subscription(**changes):
    obj = copy.deepcopy(REAL_SUBSCRIPTION)
    obj.update(changes)
    return obj


def at(seconds):
    return datetime.fromtimestamp(1790000000 + seconds, tz=dt_timezone.utc)


# --------------------------------------------------------------------- B2 checkout


@pytest.mark.django_db
def test_checkout_puts_the_metadata_on_the_subscription_itself(app, signed_post, settings):
    settings.STRIPE_TEST_SECRET_KEY = "sk_test_factice"
    Plan.objects.create(app=app, code="team1", name="1 équipe")
    stripe = MagicMock()
    stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/c/x")

    with patch.object(Plan, "price_for", return_value=MagicMock(id="price_x")), patch(
        "core.api_views.stripe_client", return_value=stripe
    ), patch("core.api_views.trial_days_for", return_value=0):
        response = signed_post(
            "/api/v1/checkout/",
            {"external_user_id": "42", "plan": "team1", "interval": "monthly",
             "success_url": "https://poker.foxugly.invalid/ok",
             "cancel_url": "https://poker.foxugly.invalid/ko"},
            app,
        )

    assert response.status_code == 200
    params = stripe.checkout.Session.create.call_args.kwargs
    assert params["subscription_data"]["metadata"] == {
        "app": "poker",
        "external_user_id": "42",
        "plan": "team1",
    }
    assert "trial_period_days" not in params["subscription_data"]


@pytest.mark.django_db
def test_checkout_keeps_the_metadata_alongside_a_trial(app, signed_post, settings):
    settings.STRIPE_TEST_SECRET_KEY = "sk_test_factice"
    Plan.objects.create(app=app, code="team1", name="1 équipe", trial_days=14)
    stripe = MagicMock()
    stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/c/x")

    with patch.object(Plan, "price_for", return_value=MagicMock(id="price_x")), patch(
        "core.api_views.stripe_client", return_value=stripe
    ), patch("core.api_views.trial_days_for", return_value=14):
        signed_post(
            "/api/v1/checkout/",
            {"external_user_id": "42", "plan": "team1", "interval": "monthly",
             "success_url": "https://poker.foxugly.invalid/ok",
             "cancel_url": "https://poker.foxugly.invalid/ko"},
            app,
        )

    data = stripe.checkout.Session.create.call_args.kwargs["subscription_data"]
    assert data["trial_period_days"] == 14
    assert data["metadata"]["external_user_id"] == "42"


# --------------------------------------------------------------- B2 résolution


@pytest.mark.django_db
def test_a_subscription_carrying_metadata_resolves_directly(app):
    obj = subscription(metadata={"app": "poker", "external_user_id": "42", "plan": "team1"})

    assert resolve_target(obj) == (app, "42")


@pytest.mark.django_db
def test_a_subscription_without_metadata_resolves_by_its_known_customer(app):
    Entitlement.objects.create(app=app, external_user_id="42", stripe_customer_id="cus_REAL")

    assert resolve_target(subscription()) == (app, "42")


@pytest.mark.django_db
def test_a_subscription_without_metadata_resolves_by_the_customer_mirror_link(app):
    from djstripe.models import Customer

    miroir = Customer.objects.create(id="cus_REAL", livemode=False)
    AppCustomer.objects.create(app=app, external_user_id="42", email="a@example.com", customer=miroir)

    assert resolve_target(subscription()) == (app, "42")


@pytest.mark.django_db
def test_legacy_subscription_resolves_by_price_app_and_customer_email(app):
    """Abonnements créés avant ce correctif : ni metadata, ni lien local. Le prix
    désigne l'app, l'e-mail du client Stripe désigne l'utilisateur — s'il est unique."""
    from djstripe.models import Customer

    Customer.objects.create(id="cus_REAL", livemode=False, stripe_data={"email": "A@example.com"})
    AppCustomer.objects.create(app=app, external_user_id="42", email="a@example.com")

    with patch("core.webhooks.apps_for_price", return_value=[app]):
        assert resolve_target(subscription()) == (app, "42")


@pytest.mark.django_db
def test_legacy_resolution_refuses_an_ambiguous_email(app):
    from djstripe.models import Customer

    Customer.objects.create(id="cus_REAL", livemode=False, stripe_data={"email": "a@example.com"})
    AppCustomer.objects.create(app=app, external_user_id="42", email="a@example.com")
    AppCustomer.objects.create(app=app, external_user_id="43", email="a@example.com")

    with patch("core.webhooks.apps_for_price", return_value=[app]):
        assert resolve_target(subscription()) == (None, None)


@pytest.mark.django_db
def test_an_unknown_customer_without_metadata_is_ignored(app):
    assert resolve_target(subscription()) == (None, None)
    assert handle_subscription_event(subscription()) is None


@pytest.mark.django_db
def test_a_real_shaped_subscription_event_grants_access_once_the_customer_is_known(app):
    Entitlement.objects.create(app=app, external_user_id="42", stripe_customer_id="cus_REAL")

    delivery = handle_subscription_event(subscription(), event_created=at(0))

    assert delivery.payload["is_paid"] is True
    assert delivery.payload["current_period_end"] is not None
    ent = Entitlement.objects.get(app=app, external_user_id="42")
    assert ent.stripe_subscription_id == "sub_1Q"
    assert ent.last_event_at == at(0)


@pytest.mark.django_db
def test_checkout_completed_links_the_customer_and_applies_a_mirrored_subscription(app):
    """Secours pour un abonnement sans metadata : la session, elle, les porte."""
    from djstripe.models import Subscription

    session = {
        "id": "cs_1",
        "object": "checkout.session",
        "client_reference_id": "poker:42",
        "metadata": {"app": "poker", "external_user_id": "42", "plan": "team1"},
        "customer": "cus_REAL",
        "subscription": "sub_1Q",
        "mode": "subscription",
    }
    with patch.object(Subscription.objects, "filter") as filtre:
        filtre.return_value.first.return_value = MagicMock(stripe_data=subscription())
        delivery = handle_checkout_completed(session)

    ent = Entitlement.objects.get(app=app, external_user_id="42")
    assert ent.stripe_customer_id == "cus_REAL"
    assert ent.is_paid is True
    assert delivery is not None


@pytest.mark.django_db
def test_checkout_completed_without_mirrored_subscription_only_links(app):
    session = {
        "metadata": {"app": "poker", "external_user_id": "42"},
        "customer": "cus_REAL",
        "subscription": "sub_absent",
    }

    assert handle_checkout_completed(session) is None
    ent = Entitlement.objects.get(app=app, external_user_id="42")
    assert ent.stripe_customer_id == "cus_REAL"
    assert ent.is_paid is False
    # La souscription suivante se rattache alors par le client.
    assert resolve_target(subscription()) == (app, "42")


# -------------------------------------------------------------------- I4 ordre


def _known(app):
    return Entitlement.objects.create(app=app, external_user_id="42", stripe_customer_id="cus_REAL")


@pytest.mark.django_db
def test_an_older_event_does_not_override_a_newer_one(app):
    _known(app)
    handle_subscription_event(subscription(status="canceled"), event_created=at(100))

    result = handle_subscription_event(subscription(status="active"), event_created=at(50))

    assert result is None
    ent = Entitlement.objects.get(app=app, external_user_id="42")
    assert ent.is_paid is False
    assert ent.status == "canceled"


@pytest.mark.django_db
def test_a_canceled_subscription_cannot_be_reopened_even_by_a_later_timestamp(app):
    """`canceled` est définitif chez Stripe : un événement qui dit le contraire
    pour le même abonnement est forcément périmé (renvoi, rejeu)."""
    _known(app)
    handle_subscription_event(subscription(status="canceled"), event_created=at(100))

    result = handle_subscription_event(subscription(status="active"), event_created=at(100))

    assert result is None
    assert Entitlement.objects.get(app=app, external_user_id="42").is_paid is False


@pytest.mark.django_db
def test_a_new_subscription_after_a_cancellation_reopens_access(app):
    _known(app)
    handle_subscription_event(subscription(status="canceled"), event_created=at(100))

    delivery = handle_subscription_event(subscription(id="sub_NEW", status="active"), event_created=at(200))

    assert delivery.payload["is_paid"] is True


@pytest.mark.django_db
def test_the_end_of_an_old_subscription_does_not_close_the_current_one(app):
    _known(app)
    handle_subscription_event(subscription(id="sub_NEW", status="active"), event_created=at(200))

    result = handle_subscription_event(subscription(id="sub_OLD", status="canceled"), event_created=at(300))

    assert result is None
    assert Entitlement.objects.get(app=app, external_user_id="42").is_paid is True


@pytest.mark.django_db
def test_newer_events_apply_in_order(app):
    _known(app)
    handle_subscription_event(subscription(status="active"), event_created=at(0))
    handle_subscription_event(subscription(status="past_due"), event_created=at(10))

    ent = Entitlement.objects.get(app=app, external_user_id="42")
    assert ent.status == "past_due"
    assert EntitlementDelivery.objects.count() == 2


@pytest.mark.django_db
def test_the_receiver_passes_the_event_timestamp(app):
    """Appel direct du récepteur : émettre le signal déclencherait aussi les
    handlers de dj-stripe, qui iraient relire l'objet chez Stripe."""
    from djstripe.models import Event

    from core.webhooks import on_subscription_event

    _known(app)
    handle_subscription_event(subscription(status="canceled"), event_created=at(100))
    event = Event(
        id="evt_late",
        type="customer.subscription.updated",
        created=at(50),
        # Autre abonnement : seul l'horodatage peut l'écarter.
        data={"object": subscription(id="sub_OTHER", status="active")},
    )

    on_subscription_event(sender=Event, event=event)

    assert Entitlement.objects.get(app=app, external_user_id="42").is_paid is False
