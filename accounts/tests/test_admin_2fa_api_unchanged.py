"""La 2FA de l'admin (admin_2fa/) ne change rien pour la console JWT, staff compris."""
import pytest
from django.core.cache import cache
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.test import APIClient

# Domaine de documentation (RFC 2606), mot de passe de test explicite.
PASSWORD = "pytest-only-not-a-real-credential"


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    """Le throttle du login (5/min) vit dans le cache LocMem, partagé entre tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
@pytest.mark.parametrize("enrolled", [False, True])
def test_staff_uses_the_console_api_with_a_password_only(django_user_model, enrolled):
    user = django_user_model.objects.create_superuser(email="ops@example.com", password=PASSWORD)
    if enrolled:
        TOTPDevice.objects.create(user=user, name="admin-2fa", confirmed=True)

    client = APIClient()
    login = client.post("/api/v1/auth/token/", {"email": user.email, "password": PASSWORD}, format="json")
    assert login.status_code == 200
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")

    me = client.get("/api/v1/auth/me/")
    assert me.status_code == 200
    assert me.json()["email"] == "ops@example.com"
    assert client.get("/api/v1/admin/dashboard/").status_code == 200


@pytest.mark.django_db
def test_health_stays_public():
    assert APIClient().get("/health/").status_code == 200
