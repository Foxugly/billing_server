"""Endpoint /api/v1/auth/password/change/ : changement de mot de passe opérateur."""
import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

URL = "/api/v1/auth/password/change/"
# Valeurs volontairement fictives, sans rien qui ressemble à un vrai secret.
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


@pytest.fixture
def api(operateur):
    c = APIClient()
    c.force_authenticate(operateur)
    return c


@pytest.mark.django_db
def test_closed_to_anonymous():
    assert APIClient().post(URL, {}, format="json").status_code == 401


@pytest.mark.django_db
def test_closed_to_a_non_staff_user(django_user_model):
    simple = django_user_model.objects.create_user(email="simple@example.com", password=CURRENT)
    c = APIClient()
    c.force_authenticate(simple)

    response = c.post(URL, {"current_password": CURRENT, "new_password": NEW}, format="json")

    assert response.status_code == 403


@pytest.mark.django_db
def test_changes_the_password(api, operateur):
    response = api.post(URL, {"current_password": CURRENT, "new_password": NEW}, format="json")

    assert response.status_code == 204
    operateur.refresh_from_db()
    assert operateur.check_password(NEW)


@pytest.mark.django_db
def test_wrong_current_password(api, operateur):
    response = api.post(URL, {"current_password": "faux", "new_password": NEW}, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "current_password_invalid"
    operateur.refresh_from_db()
    assert operateur.check_password(CURRENT)


@pytest.mark.django_db
def test_unchanged_password(api):
    response = api.post(URL, {"current_password": CURRENT, "new_password": CURRENT}, format="json")

    assert response.json()["code"] == "password_unchanged"


@pytest.mark.django_db
def test_django_validators_apply(api, operateur):
    response = api.post(URL, {"current_password": CURRENT, "new_password": "123"}, format="json")

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "password_invalid"
    assert body["messages"]
    operateur.refresh_from_db()
    assert operateur.check_password(CURRENT)


@pytest.mark.django_db
def test_missing_fields(api):
    response = api.post(URL, {}, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "invalid"


@pytest.mark.django_db
def test_throttled_after_five_attempts(api):
    for _ in range(5):
        api.post(URL, {"current_password": "faux", "new_password": NEW}, format="json")

    response = api.post(URL, {"current_password": "faux", "new_password": NEW}, format="json")

    assert response.status_code == 429
