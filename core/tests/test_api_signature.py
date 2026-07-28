import json
import time

import pytest

from core.signing import SIGNATURE_WINDOW_SECONDS, sign_payload


URL = "/api/v1/ping/"


@pytest.mark.django_db
def test_a_signed_request_is_accepted(app, signed_post):
    response = signed_post(URL, {"hello": "world"}, app)

    assert response.status_code == 200
    assert response.json() == {"app": "poker", "status": "ok"}


@pytest.mark.django_db
def test_an_unsigned_request_is_refused(client, app):
    response = client.post(URL, data="{}", content_type="application/json")

    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_a_request_signed_with_the_wrong_secret_is_refused(app, signed_post):
    response = signed_post(URL, {}, app, secret="mauvais-secret")

    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_an_expired_request_is_refused(app, signed_post):
    stale = int(time.time()) - SIGNATURE_WINDOW_SECONDS - 1

    response = signed_post(URL, {}, app, timestamp=stale)

    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_an_inactive_app_is_refused_even_with_a_perfect_signature(app, signed_post):
    """C'est le bouton d'arrêt d'urgence : couper une app sans changer son secret."""
    app.active = False
    app.save()

    response = signed_post(URL, {}, app)

    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_an_unknown_app_slug_is_refused(client, app):
    body = json.dumps({}).encode()
    ts = int(time.time())

    response = client.post(
        URL,
        data=body,
        content_type="application/json",
        HTTP_X_FOXUGLY_APP="inconnue",
        HTTP_X_FOXUGLY_TIMESTAMP=str(ts),
        HTTP_X_FOXUGLY_SIGNATURE=sign_payload(app.shared_secret, body, ts),
    )

    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_a_replayed_request_is_refused_the_second_time(client, app):
    """La requête entière est rejouée à l'identique, comme le ferait un attaquant."""
    body = json.dumps({"hello": "world"}).encode()
    ts = int(time.time())
    headers = {
        "HTTP_X_FOXUGLY_APP": app.slug,
        "HTTP_X_FOXUGLY_TIMESTAMP": str(ts),
        "HTTP_X_FOXUGLY_SIGNATURE": sign_payload(app.shared_secret, body, ts),
    }

    first = client.post(URL, data=body, content_type="application/json", **headers)
    second = client.post(URL, data=body, content_type="application/json", **headers)

    assert first.status_code == 200
    assert second.status_code in (401, 403)


@pytest.mark.django_db
def test_a_tampered_body_is_refused(client, app):
    """Signature valide pour un corps, appliquée à un autre."""
    ts = int(time.time())
    signature = sign_payload(app.shared_secret, b'{"montant": 10}', ts)

    response = client.post(
        URL,
        data=b'{"montant": 10000}',
        content_type="application/json",
        HTTP_X_FOXUGLY_APP=app.slug,
        HTTP_X_FOXUGLY_TIMESTAMP=str(ts),
        HTTP_X_FOXUGLY_SIGNATURE=signature,
    )

    assert response.status_code in (401, 403)
