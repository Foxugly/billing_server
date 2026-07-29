"""Facturation directe de prestations (§16).

Aucun appel reseau : le SDK Stripe est simule de bout en bout. Ce que ces tests
verrouillent, ce sont les regles metier qui ne se voient pas dans le dashboard --
l'ordre du cycle de vie, et le fait qu'une facture finalisee ne bouge plus.
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from core.invoicing import (
    InvoicingError,
    create_draft_invoice,
    ensure_direct_customer,
    finalize_invoice,
    mark_paid_out_of_band,
    send_invoice,
)
from core.models import AppCustomer


@pytest.fixture
def stripe_simule(db):
    """Le SDK Stripe, simule. Renvoie des objets minces mais realistes."""
    faux = MagicMock()
    faux.Customer.create.return_value = {"id": "cus_direct", "email": "client@exemple.be"}
    faux.Invoice.create.return_value = {"id": "in_1", "status": "draft", "number": None}
    faux.InvoiceItem.create.return_value = {"id": "ii_1"}
    faux.Invoice.finalize_invoice.return_value = {"id": "in_1", "status": "open", "number": "0001"}
    faux.Invoice.send_invoice.return_value = {"id": "in_1", "status": "open", "number": "0001"}
    faux.Invoice.pay.return_value = {"id": "in_1", "status": "paid", "number": "0001"}
    from djstripe.models import Customer

    client_mire = Customer.objects.create(id="cus_direct", livemode=False)
    with patch("core.invoicing.stripe_client", return_value=faux):
        with (
            patch("core.invoicing.mirror_invoice", side_effect=lambda data: data),
            patch("core.invoicing.mirror_customer", return_value=client_mire),
        ):
            yield faux


LIGNES = [
    {"description": "Audit d'architecture", "quantity": 2, "unit_amount": 75000, "tax_code": "txcd_1"},
]


@pytest.mark.django_db
def test_a_direct_customer_has_no_app(stripe_simule):
    """C'est ce qui le distingue d'un utilisateur d'un site de la flotte : aucun
    entitlement n'est calcule pour lui, aucune livraison n'est emise."""
    client = ensure_direct_customer(email="client@exemple.be", name="Exemple SRL")

    assert isinstance(client, AppCustomer)
    assert client.app is None
    assert client.email == "client@exemple.be"


@pytest.mark.django_db
def test_the_same_email_reuses_the_direct_customer(stripe_simule):
    """Sinon chaque facture creerait un doublon, et l'historique du client se
    disperserait sur plusieurs fiches."""
    premier = ensure_direct_customer(email="client@exemple.be", name="Exemple SRL")
    second = ensure_direct_customer(email="client@exemple.be", name="Exemple SRL")

    assert premier.pk == second.pk
    assert stripe_simule.Customer.create.call_count == 1


@pytest.mark.django_db
def test_a_flotte_customer_is_never_reused_as_a_direct_one(stripe_simule, app):
    """Meme adresse, deux fiches : l'une est un utilisateur de poker, l'autre un
    client de prestation. Les confondre melangerait deux facturations distinctes."""
    AppCustomer.objects.create(app=app, external_user_id="42", email="client@exemple.be")

    direct = ensure_direct_customer(email="client@exemple.be", name="Exemple SRL")

    assert direct.app is None
    assert AppCustomer.objects.filter(email="client@exemple.be").count() == 2


@pytest.mark.django_db
def test_the_draft_carries_the_lines_and_the_automatic_tax(stripe_simule):
    client = ensure_direct_customer(email="client@exemple.be", name="Exemple SRL")

    create_draft_invoice(client, LIGNES, days_until_due=30)

    parametres = stripe_simule.Invoice.create.call_args.kwargs
    assert parametres["automatic_tax"] == {"enabled": True}
    assert parametres["collection_method"] == "send_invoice"
    # Sans auto_advance=False, Stripe finaliserait tout seul : on perdrait la
    # relecture du brouillon, qui est le seul moment ou une erreur se corrige.
    assert parametres["auto_advance"] is False

    ligne = stripe_simule.InvoiceItem.create.call_args.kwargs
    assert ligne["description"] == "Audit d'architecture"
    assert ligne["quantity"] == 2
    assert ligne["unit_amount_decimal"] == Decimal(75000)
    assert ligne["tax_code"] == "txcd_1"


@pytest.mark.django_db
def test_a_draft_without_any_line_is_refused(stripe_simule):
    """Une facture vide se finalise sans bruit chez Stripe, et part a zero euro."""
    client = ensure_direct_customer(email="client@exemple.be", name="Exemple SRL")

    with pytest.raises(InvoicingError):
        create_draft_invoice(client, [], days_until_due=30)

    stripe_simule.Invoice.create.assert_not_called()


@pytest.mark.django_db
def test_a_line_without_amount_is_refused(stripe_simule):
    client = ensure_direct_customer(email="client@exemple.be", name="Exemple SRL")

    with pytest.raises(InvoicingError):
        create_draft_invoice(
            client, [{"description": "Sans montant", "quantity": 1}], days_until_due=30
        )


@pytest.mark.django_db
def test_finalising_assigns_the_number(stripe_simule):
    facture = finalize_invoice("in_1")

    stripe_simule.Invoice.finalize_invoice.assert_called_once_with("in_1")
    assert facture["number"] == "0001"


@pytest.mark.django_db
def test_marking_paid_out_of_band_is_explicit(stripe_simule):
    """Le virement direct est le cas normal a ce volume : il faut pouvoir solder
    une facture sans la faire passer par un paiement Stripe."""
    mark_paid_out_of_band("in_1")

    stripe_simule.Invoice.pay.assert_called_once_with("in_1", paid_out_of_band=True)


@pytest.mark.django_db
def test_sending_goes_through_stripe(stripe_simule):
    send_invoice("in_1")

    stripe_simule.Invoice.send_invoice.assert_called_once_with("in_1")


@pytest.mark.django_db
def test_everything_refuses_politely_without_stripe_keys(settings):
    """Tant que les cles ne sont pas seedees, le service repond 503 plutot que de
    lever une exception opaque au milieu d'un formulaire."""
    settings.STRIPE_LIVE_SECRET_KEY = ""
    settings.STRIPE_TEST_SECRET_KEY = ""

    with pytest.raises(InvoicingError):
        ensure_direct_customer(email="client@exemple.be", name="Exemple SRL")
