"""Entrées du checkout bornées, erreurs Stripe en 502 (audit M1)."""
from unittest.mock import MagicMock, patch

import pytest
from stripe import StripeError

from core.models import Entitlement, ErasedUser, Plan

BODY = {
    "external_user_id": "42",
    "plan": "team1",
    "interval": "monthly",
    "success_url": "https://poker.foxugly.invalid/ok",
    "cancel_url": "https://poker.foxugly.invalid/ko",
}


@pytest.fixture
def ready(app, settings):
    settings.STRIPE_TEST_SECRET_KEY = "sk_test_factice"
    Plan.objects.create(app=app, code="team1", name="1 équipe")
    stripe = MagicMock()
    stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/c/x")
    with patch.object(Plan, "price_for", return_value=MagicMock(id="price_x")), patch(
        "core.api_views.stripe_client", return_value=stripe
    ), patch("core.api_views.trial_days_for", return_value=0):
        yield stripe


@pytest.mark.django_db
@pytest.mark.parametrize("quantity", ["abc", 0, -1, 21, 100000, 1.5])
def test_checkout_refuses_an_out_of_range_quantity(app, ready, signed_post, quantity):
    response = signed_post("/api/v1/checkout/", {**BODY, "quantity": quantity}, app)

    assert response.status_code == 400
    assert response.json()["code"] == "bad_quantity"
    ready.checkout.Session.create.assert_not_called()


@pytest.mark.django_db
def test_checkout_refuses_an_oversized_user_id(app, ready, signed_post):
    response = signed_post("/api/v1/checkout/", {**BODY, "external_user_id": "x" * 101}, app)

    assert response.status_code == 400
    assert response.json()["code"] == "missing_user"


@pytest.mark.django_db
def test_checkout_reports_a_stripe_failure_as_502(app, ready, signed_post):
    ready.checkout.Session.create.side_effect = StripeError("boom")

    response = signed_post("/api/v1/checkout/", BODY, app)

    assert response.status_code == 502
    assert response.json()["code"] == "stripe_error"


@pytest.mark.django_db
def test_portal_reports_a_stripe_failure_as_502(app, signed_post, settings):
    Entitlement.objects.create(app=app, external_user_id="42", stripe_customer_id="cus_42")
    stripe = MagicMock()
    stripe.billing_portal.Session.create.side_effect = StripeError("boom")

    with patch("core.api_views.stripe_client", return_value=stripe):
        response = signed_post(
            "/api/v1/portal/",
            {"external_user_id": "42", "return_url": "https://poker.foxugly.invalid/x"},
            app,
        )

    assert response.status_code == 502


@pytest.mark.django_db
def test_a_new_checkout_lifts_an_old_erasure_marker(app, ready, signed_post):
    """Un identifiant réutilisé par l'app désigne un nouvel utilisateur : ses
    webhooks ne doivent pas être ignorés au titre de l'ancien."""
    ErasedUser.mark(app, "42")

    signed_post("/api/v1/checkout/", BODY, app)

    assert not ErasedUser.is_erased(app, "42")
