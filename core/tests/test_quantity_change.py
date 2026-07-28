"""Changer le nombre d'exemplaires souscrits — après l'avoir annoncé.

Le prorata d'un changement en cours de période n'est pas devinable par le client.
Une facture surprise est le meilleur moyen de perdre quelqu'un qui payait, donc
l'aperçu et l'application sont deux appels distincts.
"""
from unittest.mock import MagicMock, patch

import pytest

from core.models import Entitlement
from core.stripe_gateway import active_subscription_for, first_item_of


ABONNEMENT = {
    "id": "sub_1",
    "customer": "cus_1",
    "status": "active",
    "items": {"data": [{"id": "si_1", "quantity": 2, "price": {"id": "price_unit"}}]},
}


@pytest.fixture
def stripe_keys(settings):
    settings.STRIPE_TEST_SECRET_KEY = "sk_test_factice"
    settings.STRIPE_LIVE_MODE = False
    return settings


@pytest.fixture
def abonne(app):
    return Entitlement.objects.create(
        app=app, external_user_id="42", is_paid=True, status="active",
        plan_code="app", stripe_customer_id="cus_1",
    )


def _stripe(apercu=None):
    s = MagicMock()
    s.Subscription.list.return_value = {"data": [ABONNEMENT]}
    s.Invoice.create_preview.return_value = apercu or {
        "amount_due": 400, "currency": "eur", "period_end": 1790000000,
    }
    return s


# ------------------------------------------------------------------- helpers

def test_the_live_subscription_includes_a_trialing_one():
    """Un abonnement en essai doit rester modifiable : Stripe ne le range pas
    sous « active »."""
    s = MagicMock()
    s.Subscription.list.return_value = {"data": [{**ABONNEMENT, "status": "trialing"}]}

    assert active_subscription_for(s, "cus_1")["id"] == "sub_1"


def test_a_canceled_subscription_is_not_returned():
    s = MagicMock()
    s.Subscription.list.return_value = {"data": [{**ABONNEMENT, "status": "canceled"}]}

    assert active_subscription_for(s, "cus_1") is None


def test_first_item_reads_id_and_quantity():
    assert first_item_of(ABONNEMENT) == ("si_1", 2)
    assert first_item_of({"items": {"data": []}}) == (None, 1)


# --------------------------------------------------------------------- aperçu

@pytest.mark.django_db
def test_the_preview_announces_the_prorated_amount(app, abonne, signed_post, stripe_keys):
    stripe = _stripe()

    with patch("core.api_views.stripe_client", return_value=stripe):
        r = signed_post("/api/v1/quantity/preview/", {"external_user_id": "42", "quantity": 4}, app)

    assert r.status_code == 200
    corps = r.json()
    assert corps["current_quantity"] == 2
    assert corps["new_quantity"] == 4
    assert corps["amount_due_now"] == 400
    assert corps["currency"] == "EUR"


@pytest.mark.django_db
def test_the_preview_changes_nothing(app, abonne, signed_post, stripe_keys):
    """C'est tout l'intérêt : on annonce sans engager."""
    stripe = _stripe()

    with patch("core.api_views.stripe_client", return_value=stripe):
        signed_post("/api/v1/quantity/preview/", {"external_user_id": "42", "quantity": 4}, app)

    stripe.Subscription.modify.assert_not_called()


# ---------------------------------------------------------------- application

@pytest.mark.django_db
def test_applying_modifies_the_subscription_with_proration(app, abonne, signed_post, stripe_keys):
    stripe = _stripe()

    with patch("core.api_views.stripe_client", return_value=stripe):
        r = signed_post("/api/v1/quantity/", {"external_user_id": "42", "quantity": 4}, app)

    assert r.status_code == 200
    kwargs = stripe.Subscription.modify.call_args.kwargs
    assert kwargs["items"] == [{"id": "si_1", "quantity": 4}]
    assert kwargs["proration_behavior"] == "create_prorations"


@pytest.mark.django_db
def test_applying_does_not_recompute_locally(app, abonne, signed_post, stripe_keys):
    """Le webhook customer.subscription.updated fera le recalcul. Le dupliquer ici
    créerait deux vérités concurrentes sur le même droit."""
    stripe = _stripe()

    with patch("core.api_views.stripe_client", return_value=stripe):
        signed_post("/api/v1/quantity/", {"external_user_id": "42", "quantity": 4}, app)

    abonne.refresh_from_db()
    assert abonne.quotas == {}  # inchangé : c'est le webhook qui tranchera


# ------------------------------------------------------------------- refus

@pytest.mark.django_db
def test_a_quantity_below_one_is_refused(app, abonne, signed_post, stripe_keys):
    with patch("core.api_views.stripe_client", return_value=_stripe()):
        r = signed_post("/api/v1/quantity/", {"external_user_id": "42", "quantity": 0}, app)

    assert r.status_code == 400
    assert r.json()["code"] == "bad_quantity"


@pytest.mark.django_db
def test_a_quantity_above_the_ceiling_is_refused(app, abonne, signed_post, stripe_keys):
    with patch("core.api_views.stripe_client", return_value=_stripe()):
        r = signed_post("/api/v1/quantity/", {"external_user_id": "42", "quantity": 999}, app)

    assert r.status_code == 400


@pytest.mark.django_db
def test_a_non_numeric_quantity_is_refused(app, abonne, signed_post, stripe_keys):
    with patch("core.api_views.stripe_client", return_value=_stripe()):
        r = signed_post("/api/v1/quantity/", {"external_user_id": "42", "quantity": "beaucoup"}, app)

    assert r.status_code == 400


@pytest.mark.django_db
def test_a_user_without_subscription_is_refused(app, signed_post, stripe_keys):
    stripe = MagicMock()
    stripe.Subscription.list.return_value = {"data": []}

    with patch("core.api_views.stripe_client", return_value=stripe):
        r = signed_post("/api/v1/quantity/", {"external_user_id": "inconnu", "quantity": 3}, app)

    assert r.status_code == 400
    assert r.json()["code"] == "no_subscription"
