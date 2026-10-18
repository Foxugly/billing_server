"""Accès opérateur : limite de débit au login, révocation des sessions (audit I1, I2).

Ce compte pilote Stripe en live : un mot de passe deviné ou une session volée
coûtent de l'argent réel.
"""
import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

TOKEN = "/api/v1/auth/token/"
REFRESH = "/api/v1/auth/token/refresh/"
BLACKLIST = "/api/v1/auth/token/blacklist/"
CHANGE = "/api/v1/auth/password/change/"
CURRENT = "ancien-Mdp-de-test-42"
NEW = "nouveau-Mdp-de-test-43"


@pytest.fixture(autouse=True)
def _clear_throttle():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def operateur(db, django_user_model):
    return django_user_model.objects.create_superuser(email="ops@example.com", password=CURRENT)


def _login(client=None):
    return (client or APIClient()).post(TOKEN, {"email": "ops@example.com", "password": CURRENT}, format="json")


@pytest.mark.django_db
def test_login_is_rate_limited(operateur):
    client = APIClient()
    codes = [
        client.post(TOKEN, {"email": "ops@example.com", "password": "faux"}, format="json").status_code
        for _ in range(8)
    ]

    assert codes[:5] == [401] * 5
    assert 429 in codes[5:]


@pytest.mark.django_db
def test_the_throttle_ignores_a_forged_forwarded_for(operateur):
    """NUM_PROXIES = 1 (OPERATIONS §3.25) : un X-Forwarded-For inventé ne crée pas
    une nouvelle identité à chaque essai."""
    client = APIClient()
    codes = [
        client.post(
            TOKEN,
            {"email": "ops@example.com", "password": "faux"},
            format="json",
            HTTP_X_FORWARDED_FOR=f"10.0.0.{i}, 203.0.113.9",
        ).status_code
        for i in range(8)
    ]

    assert 429 in codes


@pytest.mark.django_db
def test_refresh_rotates_and_blacklists_the_old_token(operateur):
    old = _login().json()["refresh"]

    first = APIClient().post(REFRESH, {"refresh": old}, format="json")
    second = APIClient().post(REFRESH, {"refresh": old}, format="json")

    assert first.status_code == 200
    assert first.json()["refresh"] != old
    assert second.status_code == 401


@pytest.mark.django_db
def test_logout_blacklists_the_refresh_token(operateur):
    refresh = _login().json()["refresh"]

    assert APIClient().post(BLACKLIST, {"refresh": refresh}, format="json").status_code == 200
    assert APIClient().post(REFRESH, {"refresh": refresh}, format="json").status_code == 401


@pytest.mark.django_db
def test_password_change_revokes_every_refresh_and_returns_a_fresh_pair(operateur):
    other_session = _login().json()["refresh"]
    pair = _login().json()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {pair['access']}")

    response = client.post(CHANGE, {"current_password": CURRENT, "new_password": NEW}, format="json")

    assert response.status_code == 200
    fresh = response.json()
    assert APIClient().post(REFRESH, {"refresh": other_session}, format="json").status_code == 401
    assert APIClient().post(REFRESH, {"refresh": pair["refresh"]}, format="json").status_code == 401
    assert APIClient().post(REFRESH, {"refresh": fresh["refresh"]}, format="json").status_code == 200
