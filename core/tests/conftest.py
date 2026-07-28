"""Outils partagés par les tests de l'API signée."""
import json
import time

import pytest
from django.core.cache import cache

from core.models import App
from core.signing import sign_payload


@pytest.fixture(autouse=True)
def clear_replay_cache():
    """Le cache anti-rejeu survit d'un test à l'autre (LocMem est par processus)."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def app(db):
    return App.objects.create(slug="poker", name="Poker", base_url="https://poker-api.foxugly.com")


@pytest.fixture
def signed_post(client):
    """POST signé comme le ferait le backend d'une app de la flotte."""

    def _post(url, payload, app, secret=None, timestamp=None):
        body = json.dumps(payload).encode()
        ts = timestamp if timestamp is not None else int(time.time())
        signature = sign_payload(secret or app.shared_secret, body, ts)
        return client.post(
            url,
            data=body,
            content_type="application/json",
            HTTP_X_FOXUGLY_APP=app.slug,
            HTTP_X_FOXUGLY_TIMESTAMP=str(ts),
            HTTP_X_FOXUGLY_SIGNATURE=signature,
        )

    return _post


@pytest.fixture
def signed_get(client):
    """GET signé : le corps est vide, mais l'horodatage reste signé."""

    def _get(url, app, secret=None, timestamp=None):
        ts = timestamp if timestamp is not None else int(time.time())
        signature = sign_payload(secret or app.shared_secret, b"", ts)
        return client.get(
            url,
            HTTP_X_FOXUGLY_APP=app.slug,
            HTTP_X_FOXUGLY_TIMESTAMP=str(ts),
            HTTP_X_FOXUGLY_SIGNATURE=signature,
        )

    return _get
