"""Console : mineurs de l'audit 2026-10-18 (M6, M7)."""
import pytest
from rest_framework.test import APIClient


@pytest.fixture
def staff_client(db, django_user_model):
    c = APIClient()
    c.force_authenticate(django_user_model.objects.create_superuser(email="ops@example.com", password="x"))
    return c


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/api/v1/admin/entitlements/", "/api/v1/admin/deliveries/"])
def test_read_only_collections_refuse_a_create(staff_client, url):
    """M7 : POST sur la racine menait à un create aux champs tous en lecture seule (500)."""
    assert staff_client.post(url, {}, format="json").status_code == 405


@pytest.mark.django_db
@pytest.mark.parametrize(
    "base_url",
    ["http://poker-api.foxugly.invalid", "https://127.0.0.1", "https://evil.example.com", "https://foxugly.invalid.evil.tld"],
)
def test_an_app_base_url_must_be_an_https_fleet_host(staff_client, base_url):
    """M6 : ping et livraisons POSTent un corps signé vers cette URL."""
    response = staff_client.post(
        "/api/v1/admin/apps/", {"slug": "x", "name": "X", "base_url": base_url}, format="json"
    )

    assert response.status_code == 400
    assert "base_url" in response.json()


@pytest.mark.django_db
def test_an_https_foxugly_base_url_is_accepted(staff_client):
    response = staff_client.post(
        "/api/v1/admin/apps/",
        {"slug": "x", "name": "X", "base_url": "https://x-api.foxugly.invalid"},
        format="json",
    )

    assert response.status_code == 201
