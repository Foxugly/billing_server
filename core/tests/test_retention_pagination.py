"""Rétention et pagination (audit 2026-10-18, M5).

Avant : `EntitlementDelivery.payload`, les `djstripe.Event` et les corps de webhook
restaient à vie, et `EventViewSet.list` chargeait **tous** les événements en une
réponse.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Entitlement, EntitlementDelivery


@pytest.fixture
def staff_client(db, django_user_model):
    from rest_framework.test import APIClient

    c = APIClient()
    c.force_authenticate(django_user_model.objects.create_superuser(email="ops@example.com", password="x"))
    return c


def _event(id, created):
    from djstripe.models import Event

    return Event.objects.create(
        id=id, type="invoice.paid", livemode=False, created=created, data={"object": {}}, stripe_data={"id": id}
    )


def _delivery(entitlement, status, age_days, delivered_age_days=None):
    d = EntitlementDelivery.objects.create(entitlement=entitlement, payload={"x": 1}, status=status)
    now = timezone.now()
    EntitlementDelivery.objects.filter(pk=d.pk).update(
        created_at=now - timedelta(days=age_days),
        delivered_at=(now - timedelta(days=delivered_age_days)) if delivered_age_days is not None else None,
    )
    return d


# ------------------------------------------------------------------ rétention

@pytest.mark.django_db
def test_purge_drops_old_deliveries_but_keeps_the_last_delivered_one(app, settings):
    """La réconciliation compare au dernier payload livré : le purger lui ferait
    re-pousser chaque droit."""
    from core.retention import purge_expired_data

    settings.BILLING_DELIVERY_RETENTION_DAYS = 180
    ent = Entitlement.objects.create(app=app, external_user_id="42")
    vieux_livre = _delivery(ent, EntitlementDelivery.DELIVERED, 400, 400)
    dernier_livre = _delivery(ent, EntitlementDelivery.DELIVERED, 300, 300)
    vieil_echec = _delivery(ent, EntitlementDelivery.FAILED, 200)
    vieux_en_attente = _delivery(ent, EntitlementDelivery.PENDING, 200)
    recent = _delivery(ent, EntitlementDelivery.FAILED, 10)

    counts = purge_expired_data()

    restants = set(EntitlementDelivery.objects.values_list("pk", flat=True))
    assert restants == {dernier_livre.pk, vieux_en_attente.pk, recent.pk}
    assert vieux_livre.pk not in restants and vieil_echec.pk not in restants
    assert counts["deliveries"] == 2


@pytest.mark.django_db
def test_purge_drops_old_stripe_events_and_webhook_bodies(settings):
    from djstripe.models import Event, WebhookEventTrigger

    from core.retention import purge_expired_data

    settings.BILLING_STRIPE_EVENT_RETENTION_DAYS = 365
    now = timezone.now()
    _event("evt_old", now - timedelta(days=400))
    _event("evt_new", now - timedelta(days=5))
    old_trigger = WebhookEventTrigger.objects.create(remote_ip="127.0.0.1", headers={}, body="{}")
    WebhookEventTrigger.objects.filter(pk=old_trigger.pk).update(created=now - timedelta(days=400))
    new_trigger = WebhookEventTrigger.objects.create(remote_ip="127.0.0.1", headers={}, body="{}")

    counts = purge_expired_data()

    assert list(Event.objects.values_list("id", flat=True)) == ["evt_new"]
    assert list(WebhookEventTrigger.objects.values_list("pk", flat=True)) == [new_trigger.pk]
    assert counts["stripe_events"] == 1
    assert counts["webhook_triggers"] == 1


def test_the_purge_runs_daily_from_celery_beat(settings):
    taches = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
    assert "core.purge_expired_data" in taches


# ------------------------------------------------------------------ pagination

@pytest.mark.django_db
def test_the_events_list_is_paginated(staff_client):
    now = timezone.now()
    for i in range(60):
        _event(f"evt_{i:03d}", now - timedelta(minutes=i))

    body = staff_client.get("/api/v1/admin/events/").json()

    assert body["count"] == 60
    assert len(body["results"]) == 50
    assert body["results"][0]["id"] == "evt_000"
    assert body["next"]

    page2 = staff_client.get("/api/v1/admin/events/?page=2").json()
    assert len(page2["results"]) == 10


@pytest.mark.django_db
def test_page_size_is_capped(staff_client):
    now = timezone.now()
    for i in range(5):
        _event(f"evt_{i}", now - timedelta(minutes=i))

    body = staff_client.get("/api/v1/admin/events/?page_size=2").json()
    assert len(body["results"]) == 2

    from core.pagination import ConsolePagination

    assert ConsolePagination.max_page_size == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/admin/apps/",
        "/api/v1/admin/plans/",
        "/api/v1/admin/prices/",
        "/api/v1/admin/customers/",
        "/api/v1/admin/entitlements/",
        "/api/v1/admin/deliveries/",
        "/api/v1/admin/invoices/",
        "/api/v1/admin/events/",
    ],
)
def test_every_console_list_is_paginated(staff_client, url):
    body = staff_client.get(url).json()

    assert set(body) >= {"count", "next", "previous", "results"}


@pytest.mark.django_db
def test_a_signed_service_list_is_not_paginated(app, signed_get):
    """L'API service-à-service garde son contrat : les apps lisent une liste."""
    response = signed_get("/api/v1/plans/", app)

    assert isinstance(response.json(), (list, dict))
    assert not (isinstance(response.json(), dict) and "results" in response.json())

