"""Facturation directe de prestations (§16 du design).

Une facture ponctuelle pour un client qui n'utilise aucun site de la flotte :
pas d'entitlement, pas de livraison, pas de `Plan`. Stripe Invoicing fait le
travail — numerotation, PDF conforme, calcul de TVA, relances — et dj-stripe
mire le resultat pour que la console lise notre base plutot que l'API Stripe.

Le cycle de vie est celui de Stripe, et il est volontairement manuel :
brouillon → finalisation → envoi. `auto_advance=False` a la creation, sinon
Stripe finalise tout seul et on perd la relecture du brouillon, qui est le seul
moment ou une erreur de montant ou de code fiscal se corrige encore.
"""
import logging
from decimal import Decimal

from .models import AppCustomer
from .stripe_gateway import stripe_client

logger = logging.getLogger("billing")

# Delai de paiement par defaut, en jours. Usage courant en B2B belge.
DEFAULT_DAYS_UNTIL_DUE = 30

DEFAULT_CURRENCY = "eur"


class InvoicingError(Exception):
    """Refus metier ou Stripe indisponible. Les vues le traduisent en 400 ou 503."""


def _stripe():
    stripe = stripe_client()
    if stripe is None:
        raise InvoicingError(
            "Stripe n'est pas configure : aucune cle n'est presente. "
            "Les cles sont seedees en SSM au lot L6."
        )
    return stripe


def mirror_invoice(data):
    """Repercute une facture Stripe dans le miroir dj-stripe.

    Isole dans une fonction pour deux raisons : la console doit pouvoir lire une
    facture juste creee sans attendre le webhook, et les tests n'ont qu'un point
    a simuler.
    """
    from djstripe.models import Invoice

    return Invoice.sync_from_stripe_data(data)


def mirror_customer(data):
    """Idem pour un client. Meme raison, meme forme."""
    from djstripe.models import Customer

    return Customer.sync_from_stripe_data(data)


def ensure_direct_customer(email: str, name: str = "", address: dict | None = None) -> AppCustomer:
    """Retrouve ou cree le client direct correspondant a cette adresse.

    `app=NULL` est ce qui fait d'un client un client direct (§5). La recherche
    est donc filtree sur `app__isnull=True` : la meme adresse peut tres bien
    designer par ailleurs l'utilisateur 42 de poker, et confondre les deux
    melangerait deux facturations distinctes.
    """
    existant = AppCustomer.objects.filter(app__isnull=True, email=email).first()
    if existant is not None:
        return existant

    stripe = _stripe()
    parametres = {"email": email, "metadata": {"origin": "direct"}}
    if name:
        parametres["name"] = name
    if address:
        parametres["address"] = address

    client_stripe = stripe.Customer.create(**parametres)
    mire = mirror_customer(client_stripe)
    logger.info("direct_customer_created", extra={"email": email})
    return AppCustomer.objects.create(app=None, external_user_id="", email=email, customer=mire)


def _validate(lignes):
    if not lignes:
        raise InvoicingError("Une facture sans ligne partirait a zero euro.")

    for ligne in lignes:
        if not ligne.get("description"):
            raise InvoicingError("Chaque ligne doit porter une description.")
        if not ligne.get("unit_amount"):
            raise InvoicingError(f"Ligne « {ligne['description']} » : montant unitaire manquant.")


def create_draft_invoice(
    customer: AppCustomer,
    lignes: list[dict],
    days_until_due: int = DEFAULT_DAYS_UNTIL_DUE,
    currency: str = DEFAULT_CURRENCY,
    description: str = "",
):
    """Cree le brouillon et ses lignes. Rien n'est envoye au client a ce stade.

    Les lignes attendent `description`, `quantity`, `unit_amount` (en centimes)
    et `tax_code`. Le code fiscal porte le regime de TVA de la prestation —
    autoliquidation intra-UE, hors champ hors UE, lieu de prestation en B2C —
    et Stripe Tax ne peut rien calculer de juste sans lui.
    """
    _validate(lignes)
    stripe = _stripe()

    facture = stripe.Invoice.create(
        customer=customer.customer.id,
        collection_method="send_invoice",
        days_until_due=days_until_due,
        currency=currency,
        description=description or None,
        auto_advance=False,
        automatic_tax={"enabled": True},
        # Sans ce garde-fou, une ligne orpheline laissee par un brouillon
        # abandonne se glisserait dans la facture suivante.
        pending_invoice_items_behavior="exclude",
        metadata={"origin": "direct"},
    )

    for ligne in lignes:
        parametres = {
            "customer": customer.customer.id,
            "invoice": facture["id"],
            "description": ligne["description"],
            "quantity": int(ligne.get("quantity") or 1),
            "unit_amount_decimal": Decimal(int(ligne["unit_amount"])),
            "currency": currency,
        }
        if ligne.get("tax_code"):
            parametres["tax_code"] = ligne["tax_code"]
        stripe.InvoiceItem.create(**parametres)

    logger.info("direct_invoice_drafted", extra={"invoice": facture["id"], "lines": len(lignes)})
    return mirror_invoice(facture)


def finalize_invoice(invoice_id: str):
    """Fige la facture et lui attribue son numero. Irreversible.

    Apres finalisation, seuls l'annulation (`void`) et le paiement restent
    possibles : c'est ce que veut une piece comptable.
    """
    facture = _stripe().Invoice.finalize_invoice(invoice_id)
    logger.info("direct_invoice_finalized", extra={"invoice": invoice_id})
    return mirror_invoice(facture)


def send_invoice(invoice_id: str):
    """Envoie la facture au client, avec son lien de paiement heberge."""
    facture = _stripe().Invoice.send_invoice(invoice_id)
    logger.info("direct_invoice_sent", extra={"invoice": invoice_id})
    return mirror_invoice(facture)


def mark_paid_out_of_band(invoice_id: str):
    """Solde une facture reglee hors Stripe — un virement, typiquement.

    `paid_out_of_band` dit a Stripe que l'argent est arrive ailleurs : la facture
    passe a `paid` sans qu'aucun paiement ne soit tente.
    """
    facture = _stripe().Invoice.pay(invoice_id, paid_out_of_band=True)
    logger.info("direct_invoice_paid_out_of_band", extra={"invoice": invoice_id})
    return mirror_invoice(facture)


def void_invoice(invoice_id: str):
    """Annule une facture finalisee. Une facture emise ne se supprime pas."""
    facture = _stripe().Invoice.void_invoice(invoice_id)
    logger.info("direct_invoice_voided", extra={"invoice": invoice_id})
    return mirror_invoice(facture)


def tax_codes(recherche: str = ""):
    """Le catalogue des codes fiscaux Stripe, filtre sur un mot-cle.

    On ne fige pas de liste maison : les codes evoluent, et en inventer un
    donnerait une facture au mauvais regime de TVA sans que rien ne le signale.
    """
    codes = _stripe().TaxCode.list(limit=100)
    resultats = [
        {"id": c["id"], "name": c["name"], "description": c.get("description", "")}
        for c in codes.get("data", [])
    ]
    if recherche:
        motif = recherche.lower()
        resultats = [c for c in resultats if motif in c["name"].lower() or motif in c["description"].lower()]
    return resultats
