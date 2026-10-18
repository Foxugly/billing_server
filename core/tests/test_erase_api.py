"""Suppression d'un compte dans une app : résiliation et effacement (audit B1).

Une app qui supprime un utilisateur doit pouvoir :
- résilier ses abonnements Stripe (sinon il continue d'être prélevé sans plus
  avoir de compte pour ouvrir le portail) ;
- effacer ce que billing garde de lui (RGPD art. 17).

Stripe est toujours simulé : aucun test ne parle à l'API réelle.
"""
import time
from unittest.mock import MagicMock, patch

import pytest
from stripe import InvalidRequestError, StripeError

from core.models import App, AppCustomer, EntitlementDelivery, Entitlement, ErasedUser, Plan
from core.webhooks import handle_subscription_event

CANCEL_URL = "/api/v1/subscriptions/cancel/"
ERASE_URL = "/api/v1/erase/"


@pytest.fixture(autouse=True)
def ne_pas_livrer():
    with patch("core.tasks.deliver_entitlement.delay") as delay:
        yield delay


@pytest.fixture
def other_app(db):
    return App.objects.create(slug="tm", name="TM", base_url="https://tm-api.foxugly.invalid")


def _sub(sub_id, status="active", app="poker", user="42", **extra):
    return {
        "id": sub_id,
        "status": status,
        "customer": "cus_42",
        "metadata": {"app": app, "external_user_id": user} if app else {},
        "items": {"data": [{"price": {"id": "price_x"}, "quantity": 1}]},
        **extra,
    }


def _stripe(subs=()):
    stripe = MagicMock()
    stripe.Subscription.list.return_value = {"data": list(subs)}
    return stripe


def _paying_user(app, user="42", customer="cus_42"):
    AppCustomer.objects.create(app=app, external_user_id=user, email="joueur@example.com")
    return Entitlement.objects.create(
        app=app,
        external_user_id=user,
        is_paid=True,
        status="active",
        stripe_customer_id=customer,
        stripe_subscription_id="sub_1",
    )


# ------------------------------------------------------------------ résiliation


@pytest.mark.django_db
def test_cancel_cancels_immediately_without_refund_by_default(app, signed_post, settings):
    _paying_user(app)
    stripe = _stripe([_sub("sub_1")])

    with patch("core.api_views.stripe_client", return_value=stripe):
        response = signed_post(CANCEL_URL, {"external_user_id": "42"}, app)

    assert response.status_code == 200
    assert response.json() == {"canceled": ["sub_1"], "at_period_end": False}
    stripe.Subscription.cancel.assert_called_once_with("sub_1", prorate=False, invoice_now=False)
    stripe.Subscription.modify.assert_not_called()


@pytest.mark.django_db
def test_cancel_at_period_end_only_schedules_the_end(app, signed_post):
    _paying_user(app)
    stripe = _stripe([_sub("sub_1")])

    with patch("core.api_views.stripe_client", return_value=stripe):
        response = signed_post(CANCEL_URL, {"external_user_id": "42", "at_period_end": True}, app)

    assert response.status_code == 200
    assert response.json()["at_period_end"] is True
    stripe.Subscription.modify.assert_called_once_with("sub_1", cancel_at_period_end=True)
    stripe.Subscription.cancel.assert_not_called()


@pytest.mark.django_db
def test_cancel_skips_ended_subscriptions_and_answers_204_when_nothing_is_left(app, signed_post):
    _paying_user(app)
    stripe = _stripe([_sub("sub_old", status="canceled")])

    with patch("core.api_views.stripe_client", return_value=stripe):
        response = signed_post(CANCEL_URL, {"external_user_id": "42"}, app)

    assert response.status_code == 204
    stripe.Subscription.cancel.assert_not_called()


@pytest.mark.django_db
def test_cancel_for_an_unknown_user_is_204_without_calling_stripe(app, signed_post):
    stripe = _stripe()

    with patch("core.api_views.stripe_client", return_value=stripe):
        response = signed_post(CANCEL_URL, {"external_user_id": "inconnu"}, app)

    assert response.status_code == 204
    stripe.Subscription.list.assert_not_called()


@pytest.mark.django_db
def test_cancel_never_touches_a_subscription_of_another_app(app, other_app, signed_post):
    """Même client Stripe, abonnement d'une autre app : on n'y touche pas."""
    _paying_user(app)
    stripe = _stripe([_sub("sub_tm", app="tm"), _sub("sub_1")])

    with patch("core.api_views.stripe_client", return_value=stripe):
        response = signed_post(CANCEL_URL, {"external_user_id": "42"}, app)

    assert response.json()["canceled"] == ["sub_1"]
    stripe.Subscription.cancel.assert_called_once_with("sub_1", prorate=False, invoice_now=False)


@pytest.mark.django_db
def test_cancel_by_another_app_is_refused_by_scope(app, other_app, signed_post):
    """tm signe pour son propre utilisateur 42 : celui de poker reste intact."""
    _paying_user(app)
    stripe = _stripe([_sub("sub_1")])

    with patch("core.api_views.stripe_client", return_value=stripe):
        response = signed_post(CANCEL_URL, {"external_user_id": "42"}, other_app)

    assert response.status_code == 204
    stripe.Subscription.list.assert_not_called()
    stripe.Subscription.cancel.assert_not_called()


@pytest.mark.django_db
def test_a_legacy_subscription_without_metadata_is_recognised_by_its_price(app, signed_post):
    _paying_user(app)
    plan = Plan.objects.create(app=app, code="team1", name="1 équipe")
    stripe = _stripe([_sub("sub_1", app=None)])

    with patch("core.webhooks.plan_for_price", return_value=(plan, "monthly")), patch(
        "core.api_views.stripe_client", return_value=stripe
    ):
        response = signed_post(CANCEL_URL, {"external_user_id": "42"}, app)

    assert response.json()["canceled"] == ["sub_1"]


@pytest.mark.django_db
def test_cancel_reports_a_stripe_failure_as_502(app, signed_post):
    _paying_user(app)
    stripe = _stripe([_sub("sub_1")])
    stripe.Subscription.cancel.side_effect = StripeError("boom")

    with patch("core.api_views.stripe_client", return_value=stripe):
        response = signed_post(CANCEL_URL, {"external_user_id": "42"}, app)

    assert response.status_code == 502
    assert response.json()["code"] == "stripe_error"


@pytest.mark.django_db
def test_cancel_requires_a_signature(client, app):
    response = client.post(CANCEL_URL, {"external_user_id": "42"}, content_type="application/json")

    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_cancel_rejects_a_non_boolean_at_period_end(app, signed_post):
    _paying_user(app)

    response = signed_post(CANCEL_URL, {"external_user_id": "42", "at_period_end": "oui"}, app)

    assert response.status_code == 400


# -------------------------------------------------------------------- effacement


@pytest.mark.django_db
def test_erase_cancels_deletes_the_stripe_customer_and_anonymises(app, signed_post):
    ent = _paying_user(app)
    EntitlementDelivery.objects.create(entitlement=ent, payload=ent.payload())
    stripe = _stripe([_sub("sub_1")])

    with patch("core.api_views.stripe_client", return_value=stripe):
        response = signed_post(ERASE_URL, {"external_user_id": "42"}, app)

    assert response.status_code == 200
    assert response.json() == {
        "erased": True,
        "canceled": ["sub_1"],
        "stripe_customers_deleted": 1,
    }
    stripe.Subscription.cancel.assert_called_once_with("sub_1", prorate=False, invoice_now=False)
    stripe.Customer.delete.assert_called_once_with("cus_42")

    assert not Entitlement.objects.filter(app=app, external_user_id="42").exists()
    assert EntitlementDelivery.objects.count() == 0
    customer = AppCustomer.objects.get(app=app)
    assert customer.external_user_id.startswith("erased-")
    assert "joueur" not in customer.email
    assert customer.customer is None
    assert ErasedUser.is_erased(app, "42")


@pytest.mark.django_db
def test_erase_is_idempotent_and_answers_204_the_second_time(app, signed_post):
    _paying_user(app)
    stripe = _stripe([_sub("sub_1")])

    with patch("core.api_views.stripe_client", return_value=stripe):
        first = signed_post(ERASE_URL, {"external_user_id": "42"}, app)
        # Autre horodatage : le même envoi signé serait refusé comme rejeu.
        second = signed_post(ERASE_URL, {"external_user_id": "42"}, app, timestamp=int(time.time()) + 1)

    assert first.status_code == 200
    assert second.status_code == 204
    assert stripe.Customer.delete.call_count == 1


@pytest.mark.django_db
def test_erase_of_an_unknown_user_is_204(app, signed_post):
    stripe = _stripe()

    with patch("core.api_views.stripe_client", return_value=stripe):
        response = signed_post(ERASE_URL, {"external_user_id": "jamais-vu"}, app)

    assert response.status_code == 204
    stripe.Customer.delete.assert_not_called()


@pytest.mark.django_db
def test_erase_by_another_app_leaves_the_user_intact(app, other_app, signed_post):
    """Cloisonnement : tm ne peut pas effacer l'utilisateur 42 de poker."""
    _paying_user(app)
    stripe = _stripe([_sub("sub_1")])

    with patch("core.api_views.stripe_client", return_value=stripe):
        response = signed_post(ERASE_URL, {"external_user_id": "42"}, other_app)

    assert response.status_code == 204
    stripe.Subscription.cancel.assert_not_called()
    stripe.Customer.delete.assert_not_called()
    assert Entitlement.objects.filter(app=app, external_user_id="42").exists()
    assert AppCustomer.objects.get(app=app).email == "joueur@example.com"


@pytest.mark.django_db
def test_erase_keeps_a_stripe_customer_shared_with_another_record(app, other_app, signed_post):
    """Un `cus_…` encore référencé ailleurs n'est pas supprimé : ce serait
    résilier le client de quelqu'un d'autre."""
    _paying_user(app)
    Entitlement.objects.create(app=other_app, external_user_id="7", stripe_customer_id="cus_42")
    stripe = _stripe([_sub("sub_1")])

    with patch("core.api_views.stripe_client", return_value=stripe):
        response = signed_post(ERASE_URL, {"external_user_id": "42"}, app)

    assert response.status_code == 200
    assert response.json()["stripe_customers_deleted"] == 0
    stripe.Customer.delete.assert_not_called()


@pytest.mark.django_db
def test_erase_tolerates_a_customer_already_deleted_in_stripe(app, signed_post):
    _paying_user(app)
    stripe = _stripe()
    stripe.Customer.delete.side_effect = InvalidRequestError("No such customer: 'cus_42'", "id")

    with patch("core.api_views.stripe_client", return_value=stripe):
        response = signed_post(ERASE_URL, {"external_user_id": "42"}, app)

    assert response.status_code == 200
    assert not Entitlement.objects.filter(app=app, external_user_id="42").exists()


@pytest.mark.django_db
def test_erase_keeps_local_data_when_stripe_fails_so_a_retry_can_finish(app, signed_post):
    _paying_user(app)
    stripe = _stripe([_sub("sub_1")])
    stripe.Customer.delete.side_effect = StripeError("boom")

    with patch("core.api_views.stripe_client", return_value=stripe):
        response = signed_post(ERASE_URL, {"external_user_id": "42"}, app)

    assert response.status_code == 502
    assert Entitlement.objects.get(app=app, external_user_id="42").stripe_customer_id == "cus_42"


@pytest.mark.django_db
def test_erase_without_stripe_configured_but_with_a_customer_is_503(app, signed_post):
    _paying_user(app)

    with patch("core.api_views.stripe_client", return_value=None):
        response = signed_post(ERASE_URL, {"external_user_id": "42"}, app)

    assert response.status_code == 503
    assert Entitlement.objects.filter(app=app, external_user_id="42").exists()


@pytest.mark.django_db
def test_erase_of_a_user_without_stripe_customer_needs_no_stripe(app, signed_post):
    AppCustomer.objects.create(app=app, external_user_id="42", email="joueur@example.com")

    with patch("core.api_views.stripe_client", return_value=None):
        response = signed_post(ERASE_URL, {"external_user_id": "42"}, app)

    assert response.status_code == 200
    assert "joueur" not in AppCustomer.objects.get(app=app).email


@pytest.mark.django_db
def test_a_late_webhook_does_not_recreate_an_erased_user(app, signed_post, ne_pas_livrer):
    """Le `customer.subscription.deleted` provoqué par l'effacement arrive après :
    il ne doit ni recréer un droit ni pousser une livraison vers un compte supprimé."""
    _paying_user(app)
    stripe = _stripe([_sub("sub_1")])
    with patch("core.api_views.stripe_client", return_value=stripe):
        signed_post(ERASE_URL, {"external_user_id": "42"}, app)

    result = handle_subscription_event(_sub("sub_1", status="canceled"))

    assert result is None
    assert not Entitlement.objects.filter(app=app, external_user_id="42").exists()
    ne_pas_livrer.assert_not_called()


@pytest.mark.django_db
def test_erase_rejects_a_missing_or_oversized_user_id(app, signed_post):
    assert signed_post(ERASE_URL, {}, app).status_code == 400
    assert signed_post(ERASE_URL, {"external_user_id": "x" * 101}, app).status_code == 400
