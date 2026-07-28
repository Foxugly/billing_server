import pytest
from django.contrib.admin.sites import site

from core.models import App, AppCustomer, Entitlement, EntitlementDelivery, Plan


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
    user = django_user_model.objects.create_superuser(email="ops@foxugly.com", password="x")
    client.force_login(user)

    assert client.get("/admin/").status_code == 200


@pytest.mark.django_db
def test_admin_is_closed_to_anonymous_visitors(client):
    response = client.get("/admin/")

    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]
