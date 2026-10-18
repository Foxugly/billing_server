"""Gestes destructeurs réservés aux superusers (audit 2026-10-18, M8).

`IsAdminUser` = `is_staff` : un opérateur non superuser pouvait roter un secret
d'app, offrir un accès, émettre ou annuler une facture en live. Il garde la
lecture et les gestes sans effet commercial (ping, rejeu).
"""
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from core.models import App, Entitlement, EntitlementDelivery, Plan


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def staff_only(db, django_user_model):
    return _client(django_user_model.objects.create_user(email="staff@example.com", password="x", is_staff=True))


@pytest.fixture
def superuser(db, django_user_model):
    return _client(django_user_model.objects.create_superuser(email="root@example.com", password="x"))


@pytest.fixture
def objets(app):
    plan = Plan.objects.create(app=app, code="team1", name="Team")
    ent = Entitlement.objects.create(app=app, external_user_id="42")
    delivery = EntitlementDelivery.objects.create(entitlement=ent, payload={}, status=EntitlementDelivery.FAILED)
    return {"app": app, "plan": plan, "ent": ent, "delivery": delivery}


def _destructive(o):
    a, p, e = o["app"].pk, o["plan"].pk, o["ent"].pk
    return [
        ("post", "/api/v1/admin/apps/", {"slug": "x", "name": "X", "base_url": "https://x-api.foxugly.invalid"}),
        ("patch", f"/api/v1/admin/apps/{a}/", {"name": "Renommée"}),
        ("delete", f"/api/v1/admin/apps/{a}/", None),
        ("post", f"/api/v1/admin/apps/{a}/rotate_secret/", {}),
        ("post", "/api/v1/admin/plans/", {"app": a, "code": "n", "name": "N"}),
        ("patch", f"/api/v1/admin/plans/{p}/", {"name": "N"}),
        ("delete", f"/api/v1/admin/plans/{p}/", None),
        ("post", f"/api/v1/admin/entitlements/{e}/grant/", {}),
        ("post", "/api/v1/admin/invoices/", {}),
        ("post", "/api/v1/admin/invoices/in_1/finalize/", {}),
        ("post", "/api/v1/admin/invoices/in_1/send/", {}),
        ("post", "/api/v1/admin/invoices/in_1/mark_paid/", {}),
        ("post", "/api/v1/admin/invoices/in_1/void/", {}),
    ]


@pytest.mark.django_db
def test_a_staff_non_superuser_cannot_perform_destructive_actions(staff_only, objets):
    for method, url, data in _destructive(objets):
        response = getattr(staff_only, method)(url, data, format="json")
        assert response.status_code == 403, (method, url, response.status_code)

    objets["app"].refresh_from_db()
    assert objets["app"].name == "Poker"
    assert App.objects.count() == 1
    assert Plan.objects.count() == 1
    assert Entitlement.objects.get().source == Entitlement.STRIPE


@pytest.mark.django_db
def test_a_staff_non_superuser_keeps_read_and_harmless_actions(staff_only, objets):
    for url in [
        "/api/v1/admin/dashboard/",
        "/api/v1/admin/apps/",
        "/api/v1/admin/plans/",
        "/api/v1/admin/customers/",
        "/api/v1/admin/entitlements/",
        "/api/v1/admin/deliveries/",
        "/api/v1/admin/events/",
        "/api/v1/admin/invoices/",
    ]:
        assert staff_only.get(url).status_code == 200, url

    with patch("core.tasks.ping_app", return_value=(True, "HTTP 200")):
        assert staff_only.post(f"/api/v1/admin/apps/{objets['app'].pk}/ping/").status_code == 200
    with patch("core.tasks.deliver_entitlement.delay"):
        assert (
            staff_only.post(f"/api/v1/admin/deliveries/{objets['delivery'].pk}/replay/").status_code == 200
        )


@pytest.mark.django_db
def test_a_superuser_can_rotate_and_grant(superuser, objets):
    assert superuser.post(f"/api/v1/admin/apps/{objets['app'].pk}/rotate_secret/").status_code == 200
    with patch("core.tasks.deliver_entitlement.delay"):
        assert superuser.post(f"/api/v1/admin/entitlements/{objets['ent'].pk}/grant/", {}).status_code == 200
