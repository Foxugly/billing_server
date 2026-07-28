import json
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import Entitlement, EntitlementDelivery
from core.signing import sign_payload
from core.tasks import RETRY_SCHEDULE, deliver_entitlement, flush_pending_deliveries


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.fixture
def delivery(app):
    ent = Entitlement.objects.create(app=app, external_user_id="42", is_paid=True, status="active")
    return EntitlementDelivery.objects.create(entitlement=ent, payload=ent.payload())


@pytest.mark.django_db
def test_a_successful_delivery_is_marked_delivered(delivery):
    with patch("core.tasks.requests.post", return_value=FakeResponse(200)):
        result = deliver_entitlement(delivery.pk)

    delivery.refresh_from_db()
    assert result == EntitlementDelivery.DELIVERED
    assert delivery.status == EntitlementDelivery.DELIVERED
    assert delivery.delivered_at is not None
    assert delivery.next_retry_at is None
    assert delivery.last_error == ""


@pytest.mark.django_db
def test_a_409_counts_as_delivered(delivery):
    """L'application a déjà vu ce delivery_id : l'état est bien arrivé, une fois."""
    with patch("core.tasks.requests.post", return_value=FakeResponse(409)):
        deliver_entitlement(delivery.pk)

    delivery.refresh_from_db()
    assert delivery.status == EntitlementDelivery.DELIVERED


@pytest.mark.django_db
def test_the_delivery_id_is_included_in_the_signed_body(delivery, app):
    """C'est la clé d'idempotence côté application : sans elle, pas de dédoublonnage."""
    with patch("core.tasks.requests.post", return_value=FakeResponse(200)) as post:
        deliver_entitlement(delivery.pk)

    body = post.call_args.kwargs["data"]
    assert json.loads(body)["delivery_id"] == str(delivery.pk)


@pytest.mark.django_db
def test_the_outgoing_request_is_signed_with_the_app_secret(delivery, app):
    with patch("core.tasks.requests.post", return_value=FakeResponse(200)) as post:
        deliver_entitlement(delivery.pk)

    headers = post.call_args.kwargs["headers"]
    body = post.call_args.kwargs["data"]
    # La signature couvre desormais la methode et le chemin de l'endpoint cible.
    expected = sign_payload(
        app.shared_secret, "POST", "/api/billing/entitlement/", body,
        int(headers["X-Foxugly-Timestamp"]),
    )

    assert headers["X-Foxugly-App"] == "poker"
    assert headers["X-Foxugly-Signature"] == expected


@pytest.mark.django_db
def test_it_posts_to_the_app_entitlement_url(delivery, app):
    with patch("core.tasks.requests.post", return_value=FakeResponse(200)) as post:
        deliver_entitlement(delivery.pk)

    assert post.call_args.args[0] == "https://poker-api.foxugly.com/api/billing/entitlement/"


@pytest.mark.django_db
def test_a_network_error_schedules_the_first_retry(delivery):
    with patch("core.tasks.requests.post", side_effect=OSError("connexion refusée")):
        deliver_entitlement(delivery.pk)

    delivery.refresh_from_db()
    assert delivery.status == EntitlementDelivery.PENDING
    assert delivery.attempts == 1
    assert delivery.next_retry_at is not None
    assert "connexion refusée" in delivery.last_error


@pytest.mark.django_db
def test_a_server_error_schedules_a_retry_too(delivery):
    with patch("core.tasks.requests.post", return_value=FakeResponse(500)):
        deliver_entitlement(delivery.pk)

    delivery.refresh_from_db()
    assert delivery.status == EntitlementDelivery.PENDING
    assert delivery.last_error == "HTTP 500"


@pytest.mark.django_db
def test_the_retry_delay_grows_with_each_attempt(delivery):
    delays = []
    with patch("core.tasks.requests.post", return_value=FakeResponse(503)):
        for _ in range(3):
            before = timezone.now()
            deliver_entitlement(delivery.pk)
            delivery.refresh_from_db()
            delays.append((delivery.next_retry_at - before).total_seconds())

    assert delays[0] < delays[1] < delays[2]
    assert int(delays[0]) == pytest.approx(RETRY_SCHEDULE[0], abs=2)


@pytest.mark.django_db
def test_it_gives_up_after_the_last_step_of_the_schedule(delivery):
    with patch("core.tasks.requests.post", return_value=FakeResponse(500)):
        for _ in range(len(RETRY_SCHEDULE) + 1):
            deliver_entitlement(delivery.pk)

    delivery.refresh_from_db()
    assert delivery.status == EntitlementDelivery.FAILED
    assert delivery.next_retry_at is None


@pytest.mark.django_db
def test_an_already_delivered_payload_is_never_sent_again(delivery):
    delivery.status = EntitlementDelivery.DELIVERED
    delivery.save()

    with patch("core.tasks.requests.post") as post:
        result = deliver_entitlement(delivery.pk)

    assert result == EntitlementDelivery.DELIVERED
    post.assert_not_called()


@pytest.mark.django_db
def test_nothing_is_delivered_to_an_inactive_app(delivery, app):
    app.active = False
    app.save()

    with patch("core.tasks.requests.post") as post:
        result = deliver_entitlement(delivery.pk)

    assert result == "skipped"
    post.assert_not_called()


@pytest.mark.django_db
def test_a_missing_delivery_does_not_raise():
    import uuid

    assert deliver_entitlement(uuid.uuid4()) == "missing"


@pytest.mark.django_db
def test_flush_only_picks_deliveries_whose_deadline_has_passed(delivery, app):
    ent = delivery.entitlement
    future = EntitlementDelivery.objects.create(
        entitlement=ent,
        payload=ent.payload(),
        next_retry_at=timezone.now() + timezone.timedelta(hours=1),
    )
    delivery.next_retry_at = timezone.now() - timezone.timedelta(minutes=1)
    delivery.save()

    with patch("core.tasks.requests.post", return_value=FakeResponse(200)) as post:
        count = flush_pending_deliveries()

    assert count == 1
    assert post.call_count == 1
    future.refresh_from_db()
    assert future.status == EntitlementDelivery.PENDING
