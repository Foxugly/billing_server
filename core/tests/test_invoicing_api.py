"""L'API console de la facturation directe.

Le module `core.invoicing` a ses propres tests ; ici on verifie ce que la vue
ajoute : le cloisonnement operateur, la traduction des refus metier en codes
HTTP utilisables par un formulaire, et l'export comptable.
"""
from unittest.mock import patch

import pytest

from core.invoicing import InvoicingError
from core.models import AppCustomer


@pytest.fixture
def staff_client(db, django_user_model):
    from rest_framework.test import APIClient

    operateur = django_user_model.objects.create_superuser(email="ops@example.com", password="x")
    client = APIClient()
    client.force_authenticate(operateur)
    return client


@pytest.fixture
def stripe_configure(settings):
    """Les vues refusent en 503 tant qu'aucune cle n'est posee (jusqu'au lot L6)."""
    settings.STRIPE_TEST_SECRET_KEY = "sk_test_pour_les_tests"
    settings.STRIPE_LIVE_MODE = False
    return settings


@pytest.fixture
def facture_miree(db):
    """Une facture telle que dj-stripe la mire, avec ce que la console en lit."""
    from datetime import datetime, timezone as tz

    from djstripe.models import Customer, Invoice

    client_stripe = Customer.objects.create(id="cus_direct", livemode=False)
    AppCustomer.objects.create(app=None, external_user_id="", email="client@exemple.be",
                               customer=client_stripe)
    return Invoice.objects.create(
        id="in_1",
        customer=client_stripe,
        livemode=False,
        metadata={"origin": "direct"},
        created=datetime(2026, 7, 29, 10, 0, tzinfo=tz.utc),
        stripe_data={
            "id": "in_1",
            "number": "0001",
            "status": "open",
            "currency": "eur",
            "subtotal": 150000,
            "tax": 31500,
            "total": 181500,
            "amount_due": 181500,
            "created": 1790000000,
            "hosted_invoice_url": "https://invoice.stripe.com/i/1",
            "invoice_pdf": "https://invoice.stripe.com/i/1.pdf",
            "customer_tax_ids": [{"type": "eu_vat", "value": "BE0123456789"}],
        },
    )


CORPS = {
    "customer": {"email": "client@exemple.be", "name": "Exemple SRL"},
    "lines": [{"description": "Audit", "quantity": 2, "unit_amount": 75000, "tax_code": "txcd_1"}],
    "days_until_due": 30,
}


# ------------------------------------------------------------------ cloisonnement

@pytest.mark.django_db
def test_the_invoices_are_closed_to_anonymous(client):
    assert client.get("/api/v1/admin/invoices/").status_code in (401, 403)


@pytest.mark.django_db
def test_the_invoices_are_closed_to_a_non_staff_user(db, django_user_model):
    from rest_framework.test import APIClient

    simple = django_user_model.objects.create_user(email="simple@example.com", password="x")
    client = APIClient()
    client.force_authenticate(simple)

    assert client.get("/api/v1/admin/invoices/").status_code == 403


# ------------------------------------------------------------------ emission

@pytest.mark.django_db
def test_creating_an_invoice_finds_the_customer_then_drafts(staff_client, facture_miree, stripe_configure):
    with (
        patch("core.admin_views.ensure_direct_customer") as trouve,
        patch("core.admin_views.create_draft_invoice", return_value=facture_miree) as brouillon,
    ):
        response = staff_client.post("/api/v1/admin/invoices/", CORPS, format="json")

    assert response.status_code == 201
    assert response.json()["number"] == "0001"
    trouve.assert_called_once()
    assert trouve.call_args.kwargs["email"] == "client@exemple.be"
    assert brouillon.call_args.args[1] == CORPS["lines"]


@pytest.mark.django_db
def test_a_business_refusal_is_a_400_not_a_500(staff_client, stripe_configure):
    """Un montant manquant est une faute de saisie : le formulaire doit pouvoir
    l'afficher, pas recevoir une erreur serveur opaque."""
    with (
        patch("core.admin_views.ensure_direct_customer"),
        patch("core.admin_views.create_draft_invoice", side_effect=InvoicingError("montant manquant")),
    ):
        response = staff_client.post("/api/v1/admin/invoices/", CORPS, format="json")

    assert response.status_code == 400
    assert "montant manquant" in response.json()["detail"]


@pytest.mark.django_db
def test_stripe_not_configured_answers_503(staff_client, settings):
    settings.STRIPE_LIVE_SECRET_KEY = ""
    settings.STRIPE_TEST_SECRET_KEY = ""

    response = staff_client.post("/api/v1/admin/invoices/", CORPS, format="json")

    assert response.status_code == 503


@pytest.mark.django_db
@pytest.mark.parametrize(
    "action,cible",
    [
        ("finalize", "finalize_invoice"),
        ("send", "send_invoice"),
        ("mark_paid", "mark_paid_out_of_band"),
        ("void", "void_invoice"),
    ],
)
def test_the_lifecycle_actions_go_through_stripe(staff_client, facture_miree, action, cible):
    with patch(f"core.admin_views.{cible}", return_value=facture_miree) as appel:
        response = staff_client.post(f"/api/v1/admin/invoices/in_1/{action}/")

    assert response.status_code == 200
    appel.assert_called_once_with("in_1")


# ------------------------------------------------------------------ export comptable

@pytest.mark.django_db
def test_the_export_is_a_csv_the_accountant_can_open(staff_client, facture_miree):
    response = staff_client.get("/api/v1/admin/invoices/export/")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    lignes = response.content.decode("utf-8-sig").strip().splitlines()
    assert lignes[0].startswith("date;numero;client;")
    # Montants en euros, pas en centimes : c'est un document comptable, pas une
    # reponse d'API.
    assert "0001" in lignes[1] and "1500.00" in lignes[1] and "1815.00" in lignes[1]
    assert "BE0123456789" in lignes[1]


@pytest.mark.django_db
def test_the_export_window_excludes_what_is_outside(staff_client, facture_miree):
    response = staff_client.get("/api/v1/admin/invoices/export/?from=2030-01-01")

    lignes = response.content.decode("utf-8-sig").strip().splitlines()
    assert len(lignes) == 1  # l'en-tete seul


@pytest.mark.django_db
def test_a_draft_never_appears_in_the_accounting_export(staff_client, facture_miree):
    """Un brouillon n'a pas de numero et n'est pas une piece comptable."""
    facture_miree.stripe_data["status"] = "draft"
    facture_miree.stripe_data["number"] = None
    facture_miree.save()

    response = staff_client.get("/api/v1/admin/invoices/export/")

    assert len(response.content.decode("utf-8-sig").strip().splitlines()) == 1


# ------------------------------------------------------------------ codes fiscaux

@pytest.mark.django_db
def test_the_tax_codes_come_from_stripe_not_from_a_home_made_list(staff_client):
    """Inventer un code fiscal donnerait une facture au mauvais regime de TVA
    sans que rien ne le signale."""
    with patch("core.admin_views.tax_codes", return_value=[{"id": "txcd_1", "name": "Conseil"}]):
        response = staff_client.get("/api/v1/admin/tax-codes/?q=cons")

    assert response.status_code == 200
    assert response.json() == [{"id": "txcd_1", "name": "Conseil"}]
