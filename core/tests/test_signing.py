import time

import pytest
from django.core.cache import cache
from django.utils import timezone

from core.models import App
from core.signing import (
    SECRET_ROTATION_GRACE_SECONDS,
    SIGNATURE_WINDOW_SECONDS,
    sign_payload,
    verify_signature,
)


@pytest.fixture(autouse=True)
def clear_replay_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def app(db):
    return App.objects.create(slug="poker", name="Poker", base_url="https://poker-api.foxugly.com")


BODY = b'{"app":"poker","external_user_id":"42"}'


@pytest.mark.django_db
def test_a_correctly_signed_payload_is_accepted(app):
    ts = int(time.time())

    assert verify_signature(app, BODY, ts, sign_payload(app.shared_secret, BODY, ts)) is True


@pytest.mark.django_db
def test_a_wrong_secret_is_rejected(app):
    ts = int(time.time())

    assert verify_signature(app, BODY, ts, sign_payload("mauvais-secret", BODY, ts)) is False


@pytest.mark.django_db
def test_a_single_altered_byte_invalidates_the_signature(app):
    ts = int(time.time())
    signature = sign_payload(app.shared_secret, BODY, ts)

    assert verify_signature(app, BODY + b" ", ts, signature) is False


@pytest.mark.django_db
def test_an_expired_timestamp_is_rejected(app):
    ts = int(time.time()) - SIGNATURE_WINDOW_SECONDS - 1

    assert verify_signature(app, BODY, ts, sign_payload(app.shared_secret, BODY, ts)) is False


@pytest.mark.django_db
def test_a_timestamp_in_the_future_is_rejected(app):
    """Aussi suspect qu'un horodatage périmé : la fenêtre est bornée des deux côtés."""
    ts = int(time.time()) + SIGNATURE_WINDOW_SECONDS + 1

    assert verify_signature(app, BODY, ts, sign_payload(app.shared_secret, BODY, ts)) is False


@pytest.mark.django_db
def test_a_signature_cannot_be_replayed(app):
    ts = int(time.time())
    signature = sign_payload(app.shared_secret, BODY, ts)

    assert verify_signature(app, BODY, ts, signature) is True
    assert verify_signature(app, BODY, ts, signature) is False, "le rejeu doit être refusé"


@pytest.mark.django_db
def test_a_malformed_signature_is_rejected(app):
    ts = int(time.time())
    digest = sign_payload(app.shared_secret, BODY, ts).removeprefix("sha256=")

    assert verify_signature(app, BODY, ts, digest) is False, "le préfixe sha256= est requis"
    assert verify_signature(app, BODY, ts, "") is False
    assert verify_signature(app, BODY, ts, None) is False


@pytest.mark.django_db
def test_a_non_numeric_timestamp_is_rejected(app):
    assert verify_signature(app, BODY, "hier", sign_payload(app.shared_secret, BODY, 0)) is False
    assert verify_signature(app, BODY, None, sign_payload(app.shared_secret, BODY, 0)) is False


@pytest.mark.django_db
def test_the_previous_secret_still_works_during_the_rotation_window(app):
    old = app.shared_secret
    app.rotate_secret()
    ts = int(time.time())

    assert app.shared_secret != old
    assert verify_signature(app, BODY, ts, sign_payload(old, BODY, ts)) is True


@pytest.mark.django_db
def test_the_previous_secret_stops_working_once_the_window_has_passed(app):
    old = app.shared_secret
    app.rotate_secret()
    app.secret_rotated_at = timezone.now() - timezone.timedelta(
        seconds=SECRET_ROTATION_GRACE_SECONDS + 60
    )
    app.save()
    ts = int(time.time())

    assert verify_signature(app, BODY, ts, sign_payload(old, BODY, ts)) is False


@pytest.mark.django_db
def test_the_new_secret_works_immediately_after_rotation(app):
    app.rotate_secret()
    ts = int(time.time())

    assert verify_signature(app, BODY, ts, sign_payload(app.shared_secret, BODY, ts)) is True
