from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import App, Entitlement, EntitlementDelivery, Plan
from core.reconcile import reconcile


@pytest.fixture
def plan(app):
    return Plan.objects.create(app=app, code="team1", name="1 équipe", quotas={"teams": 1})


@pytest.mark.django_db
def test_an_expired_grace_window_closes_access_even_without_any_stripe_event(app, settings):
    """Stripe n'émet rien quand une grâce expire : sans ce balayage, l'accès
    resterait ouvert pour toujours."""
    settings.BILLING_GRACE_DAYS = 7
    Entitlement.objects.create(
        app=app,
        external_user_id="42",
        is_paid=True,
        status="past_due",
        grace_until=timezone.now() - timezone.timedelta(hours=1),
    )

    with patch("core.tasks.deliver_entitlement.delay") as delay:
        examined, changed, pushed = reconcile(push_diff=True)

    assert (examined, changed, pushed) == (1, 1, 1)
    assert Entitlement.objects.get().is_paid is False
    delay.assert_called_once()


@pytest.mark.django_db
def test_an_unchanged_entitlement_produces_no_delivery(app):
    """Sinon la réconciliation quotidienne inonderait la flotte de livraisons inutiles."""
    ent = Entitlement.objects.create(app=app, external_user_id="42", is_paid=True, status="active")
    EntitlementDelivery.objects.create(
        entitlement=ent,
        payload=ent.payload(),
        status=EntitlementDelivery.DELIVERED,
        delivered_at=timezone.now(),
    )

    examined, changed, pushed = reconcile(push_diff=True)

    assert (examined, changed, pushed) == (1, 0, 0)


@pytest.mark.django_db
def test_an_entitlement_never_delivered_is_considered_changed(app):
    Entitlement.objects.create(app=app, external_user_id="42", is_paid=True, status="active")

    with patch("core.tasks.deliver_entitlement.delay"):
        _, changed, pushed = reconcile(push_diff=True)

    assert (changed, pushed) == (1, 1)


@pytest.mark.django_db
def test_without_push_diff_nothing_is_emitted(app):
    Entitlement.objects.create(app=app, external_user_id="42", is_paid=True, status="active")

    _, changed, pushed = reconcile(push_diff=False)

    assert changed == 1
    assert pushed == 0
    assert EntitlementDelivery.objects.count() == 0


@pytest.mark.django_db
def test_the_app_filter_restricts_the_scope(app):
    other = App.objects.create(slug="tm", name="TM", base_url="https://tm-api.foxugly.com")
    Entitlement.objects.create(app=app, external_user_id="42")
    Entitlement.objects.create(app=other, external_user_id="7")

    examined, _, _ = reconcile(app_slug="poker")

    assert examined == 1


@pytest.mark.django_db
def test_a_manual_entitlement_in_grace_is_left_alone(app, settings):
    """Un accès offert ne doit pas être fermé par le balayage de grâce."""
    settings.BILLING_GRACE_DAYS = 7
    Entitlement.objects.create(
        app=app,
        external_user_id="42",
        is_paid=True,
        source=Entitlement.MANUAL,
        grace_until=timezone.now() - timezone.timedelta(hours=1),
    )

    reconcile(push_diff=False)

    assert Entitlement.objects.get().is_paid is True


@pytest.mark.django_db
def test_the_management_command_runs_and_reports(app, capsys):
    Entitlement.objects.create(app=app, external_user_id="42")

    call_command("sync_entitlements", "--app", "poker")

    assert "examiné" in capsys.readouterr().out
