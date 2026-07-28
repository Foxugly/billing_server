"""API de la console : elle est réservée aux opérateurs, et son cloisonnement
vis-à-vis de l'API service-à-service est une propriété de sécurité, pas un détail
d'implémentation.
"""
from unittest.mock import patch

import pytest

from core.models import App, Entitlement, EntitlementDelivery, Plan


@pytest.fixture
def staff(db, django_user_model):
    return django_user_model.objects.create_superuser(email="ops@example.com", password="x")


@pytest.fixture
def plain_user(db, django_user_model):
    return django_user_model.objects.create_user(email="simple@example.com", password="x")


@pytest.fixture
def staff_client(staff):
    from rest_framework.test import APIClient

    c = APIClient()
    c.force_authenticate(staff)
    return c


# ------------------------------------------------------------------ cloisonnement

@pytest.mark.django_db
def test_the_admin_api_is_closed_to_anonymous(client):
    assert client.get("/api/admin/apps/").status_code in (401, 403)


@pytest.mark.django_db
def test_the_admin_api_is_closed_to_a_non_staff_user(plain_user):
    from rest_framework.test import APIClient

    c = APIClient()
    c.force_authenticate(plain_user)

    assert c.get("/api/admin/apps/").status_code == 403


@pytest.mark.django_db
def test_a_valid_hmac_signature_does_not_open_the_admin_api(app, signed_get):
    """Les deux authentifications sont disjointes : une app de la flotte, même
    parfaitement signée, ne doit pas pouvoir lire la facturation des autres."""
    response = signed_get("/api/admin/apps/", app)

    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_the_login_refuses_a_non_staff_account(client, plain_user):
    """Mieux vaut un refus clair qu'un jeton valide mais inerte."""
    response = client.post(
        "/api/auth/token/",
        {"email": "simple@example.com", "password": "x"},
        content_type="application/json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_the_login_issues_a_token_for_an_operator(client, staff):
    response = client.post(
        "/api/auth/token/",
        {"email": "ops@example.com", "password": "x"},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert "access" in response.json() and "refresh" in response.json()


# -------------------------------------------------------------------------- apps

@pytest.mark.django_db
def test_the_shared_secret_is_never_exposed(staff_client, app):
    """Il n'a rien à faire dans un bundle SPA ni dans un cache navigateur."""
    body = staff_client.get("/api/admin/apps/").json()

    assert "shared_secret" not in str(body)


@pytest.mark.django_db
def test_rotating_a_secret_returns_it_exactly_once(staff_client, app):
    old = app.shared_secret

    response = staff_client.post(f"/api/admin/apps/{app.id}/rotate_secret/")

    assert response.status_code == 200
    assert response.json()["shared_secret"] != old
    # Et il n'est plus relisible ensuite.
    assert "shared_secret" not in str(staff_client.get("/api/admin/apps/").json())


@pytest.mark.django_db
def test_the_ping_runs_server_side(staff_client, app):
    with patch("core.tasks.ping_app", return_value=(True, "HTTP 200")) as pinged:
        response = staff_client.post(f"/api/admin/apps/{app.id}/ping/")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    pinged.assert_called_once()


# ------------------------------------------------------------------------- plans

@pytest.mark.django_db
def test_plans_can_be_filtered_by_app(staff_client, app):
    other = App.objects.create(slug="tm", name="TM", base_url="https://tm-api.foxugly.com")
    Plan.objects.create(app=app, code="team1", name="1 équipe")
    Plan.objects.create(app=other, code="club", name="Club")

    body = staff_client.get("/api/admin/plans/?app=poker").json()
    results = body["results"] if isinstance(body, dict) else body

    assert [p["code"] for p in results] == ["team1"]


# ------------------------------------------------------------------------- droits

@pytest.mark.django_db
def test_granting_access_marks_the_entitlement_manual_and_pushes_it(staff_client, app):
    """`manual` est la seule façon correcte d'offrir un accès : tout recalcul le
    respecte, là où éditer les champs dérivés serait écrasé au prochain webhook."""
    ent = Entitlement.objects.create(app=app, external_user_id="42")

    with patch("core.tasks.deliver_entitlement.delay") as delivered:
        response = staff_client.post(
            f"/api/admin/entitlements/{ent.id}/grant/",
            {"quotas": {"teams": 3}},
            format="json",
        )

    assert response.status_code == 200
    ent.refresh_from_db()
    assert ent.source == Entitlement.MANUAL
    assert ent.is_paid is True and ent.quotas == {"teams": 3}
    delivered.assert_called_once()


@pytest.mark.django_db
def test_a_granted_access_survives_a_recompute(staff_client, app):
    from core.services import recompute_entitlement

    ent = Entitlement.objects.create(app=app, external_user_id="42")
    with patch("core.tasks.deliver_entitlement.delay"):
        staff_client.post(f"/api/admin/entitlements/{ent.id}/grant/")

    recompute_entitlement(app, "42", stripe_status="canceled")

    ent.refresh_from_db()
    assert ent.is_paid is True


# -------------------------------------------------------------------- livraisons

@pytest.mark.django_db
def test_replaying_a_failed_delivery_requeues_it(staff_client, app):
    ent = Entitlement.objects.create(app=app, external_user_id="42")
    delivery = EntitlementDelivery.objects.create(
        entitlement=ent, payload=ent.payload(), status=EntitlementDelivery.FAILED, attempts=7
    )

    with patch("core.tasks.deliver_entitlement.delay") as delivered:
        response = staff_client.post(f"/api/admin/deliveries/{delivery.pk}/replay/")

    assert response.status_code == 200
    delivery.refresh_from_db()
    assert delivery.status == EntitlementDelivery.PENDING
    delivered.assert_called_once()


@pytest.mark.django_db
def test_deliveries_can_be_filtered_by_status(staff_client, app):
    ent = Entitlement.objects.create(app=app, external_user_id="42")
    EntitlementDelivery.objects.create(entitlement=ent, payload={}, status=EntitlementDelivery.FAILED)
    EntitlementDelivery.objects.create(entitlement=ent, payload={}, status=EntitlementDelivery.DELIVERED)

    body = staff_client.get("/api/admin/deliveries/?status=failed").json()
    results = body["results"] if isinstance(body, dict) else body

    assert len(results) == 1
    assert results[0]["status"] == "failed"


# --------------------------------------------------------------------- dashboard

@pytest.mark.django_db
def test_the_dashboard_counts_paid_entitlements_per_app(staff_client, app):
    Entitlement.objects.create(app=app, external_user_id="1", is_paid=True)
    Entitlement.objects.create(app=app, external_user_id="2", is_paid=False)

    body = staff_client.get("/api/admin/dashboard/").json()

    poker = next(a for a in body["apps"] if a["slug"] == "poker")
    assert (poker["paid"], poker["total"]) == (1, 2)


@pytest.mark.django_db
def test_the_dashboard_reports_failed_deliveries(staff_client, app):
    ent = Entitlement.objects.create(app=app, external_user_id="1")
    EntitlementDelivery.objects.create(entitlement=ent, payload={}, status=EntitlementDelivery.FAILED)

    body = staff_client.get("/api/admin/dashboard/").json()

    assert body["deliveries"]["failed"] == 1


@pytest.mark.django_db
def test_the_mrr_is_zero_without_any_priced_plan(staff_client, app):
    Entitlement.objects.create(app=app, external_user_id="1", is_paid=True, plan_code="team1")

    assert staff_client.get("/api/admin/dashboard/").json()["mrr_cents"] == 0
