"""Endpoint /api/v1/auth/me/ : l'en-tête de la console et la page Profil."""
import pytest
from rest_framework.test import APIClient

URL = "/api/v1/auth/me/"


@pytest.fixture
def operateur(db, django_user_model):
    return django_user_model.objects.create_superuser(
        email="ops@example.com", password="x", first_name="Ada", last_name="Lovelace"
    )


@pytest.fixture
def api(operateur):
    c = APIClient()
    c.force_authenticate(operateur)
    return c


@pytest.mark.django_db
def test_me_is_closed_to_anonymous():
    assert APIClient().get(URL).status_code == 401


@pytest.mark.django_db
def test_me_is_closed_to_a_non_staff_user(django_user_model):
    simple = django_user_model.objects.create_user(email="simple@example.com", password="x")
    c = APIClient()
    c.force_authenticate(simple)

    assert c.get(URL).status_code == 403
    assert c.patch(URL, {"firstName": "X"}, format="json").status_code == 403


@pytest.mark.django_db
def test_me_returns_the_profile(api, operateur):
    body = api.get(URL).json()

    assert body == {
        "id": operateur.id,
        "email": "ops@example.com",
        "firstName": "Ada",
        "lastName": "Lovelace",
        "displayName": "Ada Lovelace",
        "isStaff": True,
        "isSuperuser": True,
    }


@pytest.mark.django_db
def test_patch_updates_the_names_and_trims_them(api, operateur):
    response = api.patch(URL, {"firstName": "  Grace ", "lastName": "Hopper"}, format="json")

    assert response.status_code == 200
    assert response.json()["displayName"] == "Grace Hopper"
    operateur.refresh_from_db()
    assert (operateur.first_name, operateur.last_name) == ("Grace", "Hopper")


@pytest.mark.django_db
def test_patch_cannot_change_email_or_privileges(django_user_model):
    simple = django_user_model.objects.create_user(
        email="simple@example.com", password="x", is_staff=True
    )
    c = APIClient()
    c.force_authenticate(simple)

    response = c.patch(
        URL,
        {"email": "pirate@example.com", "isSuperuser": True, "isStaff": False},
        format="json",
    )

    assert response.status_code == 200
    simple.refresh_from_db()
    assert simple.email == "simple@example.com"
    assert simple.is_superuser is False
    assert simple.is_staff is True


@pytest.mark.django_db
def test_names_can_be_cleared(api):
    response = api.patch(URL, {"firstName": "", "lastName": ""}, format="json")

    assert response.status_code == 200
    # get_full_name retombe sur l'email quand le nom est vide.
    assert response.json()["displayName"] == "ops@example.com"


@pytest.mark.django_db
def test_too_long_names_are_rejected(api):
    response = api.patch(URL, {"firstName": "x" * 151}, format="json")

    assert response.status_code == 400
    assert "firstName" in response.json()


@pytest.mark.django_db
def test_put_is_not_allowed(api):
    assert api.put(URL, {"firstName": "A", "lastName": "B"}, format="json").status_code == 405
