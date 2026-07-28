from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import App, Entitlement, Plan
from core.services import period_end_of, recompute_entitlement


@pytest.fixture
def app(db):
    return App.objects.create(slug="poker", name="Poker", base_url="https://poker-api.foxugly.com")


@pytest.fixture
def plan(app):
    return Plan.objects.create(app=app, code="team1", name="1 équipe", quotas={"teams": 1})


def test_period_end_reads_the_subscription_item_first():
    """Stripe a déplacé current_period_end au niveau des items : le lire à la racine
    renvoie None en silence, et un abonnement payé paraît expiré (§14)."""
    sub = {"items": {"data": [{"current_period_end": 1790000000}]}}

    assert period_end_of(sub) is not None


def test_period_end_falls_back_to_the_root_for_older_payloads():
    sub = {"current_period_end": 1790000000, "items": {"data": [{}]}}

    assert period_end_of(sub) is not None


def test_period_end_prefers_the_item_over_the_root_when_both_are_present():
    sub = {"current_period_end": 1000000000, "items": {"data": [{"current_period_end": 1790000000}]}}

    assert period_end_of(sub).year > 2020


def test_period_end_is_none_when_absent_everywhere():
    assert period_end_of({"items": {"data": []}}) is None
    assert period_end_of({}) is None


@pytest.mark.django_db
def test_without_any_subscription_the_user_is_not_paid(app):
    ent = recompute_entitlement(app, "42")

    assert ent.is_paid is False
    assert ent.plan_code == ""
    assert ent.quotas == {}


@pytest.mark.django_db
def test_manual_source_is_never_overwritten_by_a_recompute(app):
    """Un accès offert depuis la console ne doit pas être effacé par un webhook Stripe."""
    Entitlement.objects.create(
        app=app, external_user_id="42", is_paid=True, source=Entitlement.MANUAL, quotas={"teams": 9}
    )

    ent = recompute_entitlement(app, "42")

    assert ent.source == Entitlement.MANUAL
    assert ent.is_paid is True
    assert ent.quotas == {"teams": 9}


@pytest.mark.django_db
def test_grace_period_keeps_access_open_after_a_failed_payment(app, plan, settings):
    settings.BILLING_GRACE_DAYS = 7
    Entitlement.objects.create(
        app=app, external_user_id="42", is_paid=True, status="active", plan_code="team1"
    )

    ent = recompute_entitlement(app, "42", stripe_status="past_due", plan=plan, interval="monthly")

    assert ent.status == "past_due"
    assert ent.is_paid is True, "l'accès reste ouvert pendant la grâce"
    assert ent.grace_until is not None
    assert ent.grace_until > timezone.now()


@pytest.mark.django_db
def test_the_grace_window_does_not_restart_on_each_failed_invoice(app, plan, settings):
    """Sinon un échec quotidien prolongerait la grâce indéfiniment."""
    settings.BILLING_GRACE_DAYS = 7
    first = recompute_entitlement(app, "42", stripe_status="past_due", plan=plan, interval="monthly")
    initial_deadline = first.grace_until

    second = recompute_entitlement(app, "42", stripe_status="past_due", plan=plan, interval="monthly")

    assert second.grace_until == initial_deadline


@pytest.mark.django_db
def test_access_closes_once_the_grace_period_has_expired(app, plan, settings):
    settings.BILLING_GRACE_DAYS = 7
    Entitlement.objects.create(
        app=app,
        external_user_id="42",
        is_paid=True,
        status="past_due",
        grace_until=timezone.now() - timedelta(days=1),
    )

    ent = recompute_entitlement(app, "42", stripe_status="past_due", plan=plan, interval="monthly")

    assert ent.is_paid is False


@pytest.mark.django_db
def test_grace_days_zero_closes_access_at_the_first_failure(app, plan, settings):
    settings.BILLING_GRACE_DAYS = 0

    ent = recompute_entitlement(app, "42", stripe_status="past_due", plan=plan, interval="monthly")

    assert ent.is_paid is False


@pytest.mark.django_db
def test_a_canceled_subscription_closes_access_even_within_the_grace_window(app, plan, settings):
    settings.BILLING_GRACE_DAYS = 7

    ent = recompute_entitlement(app, "42", stripe_status="canceled", plan=plan, interval="monthly")

    assert ent.is_paid is False
    assert ent.grace_until is None


@pytest.mark.django_db
def test_recovering_from_past_due_clears_the_grace_deadline(app, plan, settings):
    settings.BILLING_GRACE_DAYS = 7
    recompute_entitlement(app, "42", stripe_status="past_due", plan=plan, interval="monthly")

    ent = recompute_entitlement(app, "42", stripe_status="active", plan=plan, interval="monthly")

    assert ent.is_paid is True
    assert ent.grace_until is None


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["active", "trialing"])
def test_active_and_trialing_grant_the_plan_quotas(app, plan, status):
    ent = recompute_entitlement(app, "42", stripe_status=status, plan=plan, interval="monthly")

    assert ent.is_paid is True
    assert ent.plan_code == "team1"
    assert ent.interval == "monthly"
    assert ent.quotas == {"teams": 1}


@pytest.mark.django_db
def test_the_stripe_customer_id_is_kept_when_a_later_event_omits_it(app, plan):
    recompute_entitlement(app, "42", stripe_status="active", plan=plan, interval="monthly",
                          stripe_customer_id="cus_123")

    ent = recompute_entitlement(app, "42", stripe_status="canceled", plan=plan, interval="monthly")

    assert ent.stripe_customer_id == "cus_123"
