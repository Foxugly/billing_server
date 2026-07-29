"""Les evenements Stripe vus depuis la console, et leur rejeu.

Le compte Stripe est partage par toute la flotte : la liste contient donc aussi
des evenements qui ne nous concernent pas. La page doit le dire, sinon un
operateur cherche pendant dix minutes pourquoi un evenement « n'a rien fait ».
"""
from unittest.mock import patch

import pytest

from core.models import App


@pytest.fixture
def staff_client(db, django_user_model):
    from rest_framework.test import APIClient

    operateur = django_user_model.objects.create_superuser(email="ops@example.com", password="x")
    client = APIClient()
    client.force_authenticate(operateur)
    return client


@pytest.fixture
def app(db):
    return App.objects.create(slug="poker", name="Poker", base_url="https://poker-api.foxugly.com")


def _event(id, type, objet=None, created=None):
    from datetime import datetime, timezone as tz

    from djstripe.models import Event

    return Event.objects.create(
        id=id,
        type=type,
        livemode=False,
        created=created or datetime(2026, 7, 29, 10, 0, tzinfo=tz.utc),
        data={"object": objet or {}},
        stripe_data={"id": id, "type": type},
    )


ABONNEMENT = {"metadata": {"app": "poker", "external_user_id": "42"}, "status": "active"}


@pytest.mark.django_db
def test_the_events_are_closed_to_anonymous(client):
    assert client.get("/api/v1/admin/events/").status_code in (401, 403)


@pytest.mark.django_db
def test_the_list_says_which_events_we_act_on(staff_client, app):
    _event("evt_1", "customer.subscription.updated", ABONNEMENT)
    _event("evt_2", "invoice.payment_succeeded", {})

    lignes = staff_client.get("/api/v1/admin/events/").json()
    par_id = {e["id"]: e for e in lignes}

    assert par_id["evt_1"]["handled"] is True
    assert par_id["evt_1"]["app_slug"] == "poker"
    assert par_id["evt_1"]["external_user_id"] == "42"
    # Un evenement d'un autre produit du meme compte Stripe : on n'y touche pas,
    # et la page doit le montrer plutot que de laisser croire a un echec.
    assert par_id["evt_2"]["handled"] is False
    assert par_id["evt_2"]["app_slug"] == ""


@pytest.mark.django_db
def test_an_event_we_act_on_but_cannot_attribute_is_flagged(staff_client, app):
    """Ni `metadata` ni `client_reference_id` : le webhook l'a ignore en silence.
    C'est exactement le cas qu'un operateur doit pouvoir reperer."""
    _event("evt_3", "customer.subscription.updated", {"status": "active"})

    ligne = staff_client.get("/api/v1/admin/events/").json()[0]

    assert ligne["handled"] is True
    assert ligne["app_slug"] == ""


@pytest.mark.django_db
def test_the_newest_event_comes_first(staff_client, app):
    from datetime import datetime, timezone as tz

    _event("evt_vieux", "customer.subscription.updated", ABONNEMENT,
           created=datetime(2026, 7, 1, tzinfo=tz.utc))
    _event("evt_recent", "customer.subscription.updated", ABONNEMENT,
           created=datetime(2026, 7, 29, tzinfo=tz.utc))

    lignes = staff_client.get("/api/v1/admin/events/").json()

    assert [e["id"] for e in lignes] == ["evt_recent", "evt_vieux"]


@pytest.mark.django_db
def test_the_type_filter_narrows_the_list(staff_client, app):
    _event("evt_1", "customer.subscription.updated", ABONNEMENT)
    _event("evt_2", "invoice.payment_succeeded", {})

    lignes = staff_client.get("/api/v1/admin/events/?type=subscription").json()

    assert [e["id"] for e in lignes] == ["evt_1"]


@pytest.mark.django_db
def test_only_the_events_we_act_on_can_be_kept(staff_client, app):
    _event("evt_1", "customer.subscription.updated", ABONNEMENT)
    _event("evt_2", "invoice.payment_succeeded", {})

    lignes = staff_client.get("/api/v1/admin/events/?handled=true").json()

    assert [e["id"] for e in lignes] == ["evt_1"]


# ------------------------------------------------------------------ rejeu

@pytest.mark.django_db
def test_replaying_runs_the_handler_again(staff_client, app):
    """Le vrai usage : un bug de traitement corrige, on repasse les evenements
    qui en ont souffert sans rien demander a Stripe."""
    _event("evt_1", "customer.subscription.updated", ABONNEMENT)

    with patch("core.webhooks.handle_subscription_event") as traite:
        response = staff_client.post("/api/v1/admin/events/evt_1/replay/")

    assert response.status_code == 200
    traite.assert_called_once()
    assert traite.call_args.args[0]["metadata"]["app"] == "poker"


@pytest.mark.django_db
def test_replaying_an_event_we_do_not_act_on_is_refused(staff_client, app):
    """Renvoyer le signal ne ferait rien du tout : autant le dire en 400 plutot
    que de laisser croire a un rejeu reussi."""
    _event("evt_2", "invoice.payment_succeeded", {})

    response = staff_client.post("/api/v1/admin/events/evt_2/replay/")

    assert response.status_code == 400


@pytest.mark.django_db
def test_replaying_an_unknown_event_is_a_404(staff_client, app):
    assert staff_client.post("/api/v1/admin/events/evt_absent/replay/").status_code == 404
