from pathlib import Path

import pytest
from django.contrib.admin.sites import site

from admin_2fa.testing import force_admin_login
from core.models import App, AppCustomer, Entitlement, EntitlementDelivery, Plan

# Domaine de documentation (RFC 2606) : un email au domaine de l'entreprise accolé
# à un "password=" déclenche les scanners de secrets pour rien.
TEST_PASSWORD = "pytest-only-not-a-real-credential"


@pytest.mark.parametrize("model", [App, Plan, AppCustomer, Entitlement, EntitlementDelivery])
def test_every_business_model_is_registered_in_the_admin(model):
    assert model in site._registry


def test_the_shared_secret_is_not_editable_by_hand():
    """Sa rotation doit passer par le geste dédié, pas par un champ de formulaire."""
    assert "shared_secret" in site._registry[App].readonly_fields


def test_derived_entitlement_fields_are_read_only():
    """Les éditer créerait un état que le prochain webhook écraserait en silence."""
    readonly = site._registry[Entitlement].readonly_fields

    assert {"status", "current_period_end", "grace_until"} <= set(readonly)


@pytest.mark.django_db
def test_admin_index_is_reachable_by_a_staff_user(client, django_user_model):
    user = django_user_model.objects.create_superuser(email="ops@example.com", password=TEST_PASSWORD)
    force_admin_login(client, user)  # l'admin exige la 2FA (admin_2fa/)

    assert client.get("/admin/").status_code == 200


@pytest.mark.django_db
def test_admin_is_closed_to_anonymous_visitors(client):
    response = client.get("/admin/")

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_admin_2fa_steps_share_the_login_rate_limit():
    """nginx : le second facteur est dans la même zone que /admin/login/ (admin_2fa/)."""
    vhost = (Path(__file__).resolve().parents[2] / "deploy" / "nginx" / "billing-api.conf").read_text(
        encoding="utf-8"
    )
    block = vhost.split("location ^~ /admin/2fa/ {", 1)[1].split("}", 1)[0]

    assert "limit_req zone=billing_admin_login" in block
    assert "proxy_pass http://127.0.0.1:8007;" in block
