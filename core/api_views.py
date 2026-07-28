"""API service-à-service. Toutes les vues exigent une signature HMAC (§8).

Aucune de ces routes n'est atteignable depuis un navigateur : elles sont appelées
par les backends de la flotte, jamais par un SPA. C'est le backend de l'app qui
connaît l'identité de son utilisateur — le central ne fait que lui faire confiance,
signature à l'appui.
"""
import logging
from datetime import datetime

from django.utils.timezone import get_current_timezone
from djstripe.models import Customer as DjstripeCustomer
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AppCustomer, Entitlement, Plan
from .permissions import HasValidAppSignature
from .services import period_end_of, recompute_entitlement
from .webhooks import plan_for_price, price_id_of, quantity_of
from .stripe_gateway import (
    active_subscription_for,
    apply_quantity_change,
    preview_quantity_change,
    stripe_client,
    stripe_configured,
    trial_days_for,
    url_is_allowed_for,
)

logger = logging.getLogger("billing")

# Borne haute de l'ajustement en ligne. Au-dela, un forfait illimite revient
# moins cher : c'est a l'application de l'y orienter, pas au tunnel de paiement.
MAX_UNITS_PER_CHECKOUT = 20


def error(code, detail, http_status):
    return Response({"code": code, "detail": detail}, status=http_status)


class SignedServiceView(APIView):
    """Base commune : pas d'authentification utilisateur, signature obligatoire."""

    authentication_classes = []
    permission_classes = [HasValidAppSignature]


class PingView(SignedServiceView):
    """Vérifie la connectivité et le secret partagé, sans effet de bord.

    C'est ce qu'appellera le bouton « tester la connectivité » de la console.
    """

    def post(self, request):
        return Response({"app": request.billing_app.slug, "status": "ok"})


def known_stripe_customer_id(app, external_user_id: str) -> str:
    """L'identifiant Stripe (`cus_…`) déjà connu pour ce couple (app, utilisateur).

    Il vit sur l'`Entitlement`, écrit par le webhook. `AppCustomer.customer` est
    une FK vers le miroir dj-stripe dont la valeur brute est un entier interne,
    pas un `cus_…` : la lire comme un identifiant Stripe est un piège, et c'était
    la cause d'un checkout qui recréait un client à chaque paiement.
    """
    entitlement = Entitlement.objects.filter(
        app=app, external_user_id=external_user_id
    ).only("stripe_customer_id").first()
    return (entitlement.stripe_customer_id or "") if entitlement else ""


def serialize_price(price):
    if price is None:
        return None
    return {
        "id": price.id,
        "amount": price.unit_amount,
        "currency": (price.currency or "").upper(),
    }


class PlansView(SignedServiceView):
    """Le catalogue de l'app appelante.

    Un plan sans aucun prix configuré est **exclu** : l'afficher mènerait à un
    bouton d'achat qui échoue, ce qui est pire que de ne rien afficher.
    """

    def get(self, request):
        plans = (
            Plan.objects.filter(app=request.billing_app, active=True, public=True)
            .select_related("price_monthly", "price_yearly")
            .order_by("sort_order", "code")
        )
        payload = []
        for plan in plans:
            prices = {
                "monthly": serialize_price(plan.price_monthly),
                "yearly": serialize_price(plan.price_yearly),
            }
            if not any(prices.values()):
                continue
            payload.append(
                {
                    "code": plan.code,
                    "name": plan.name,
                    "description": plan.description,
                    "quotas": plan.quotas,
                    "per_unit_quota_key": plan.per_unit_quota_key,
                    # Sans ça, une app consommatrice ne peut pas annoncer l'essai
                    # avant l'achat. Ce n'est PAS l'essai auquel ce client-ci a
                    # droit — il n'est accordé qu'à une première souscription
                    # d'une seule unité (cf. `trial_days_for`) — mais le montant
                    # exact reste affiché par Stripe Checkout avant paiement.
                    "trial_days": plan.trial_days,
                    "prices": prices,
                }
            )
        return Response(payload)


class EntitlementView(SignedServiceView):
    """État courant d'un droit, recalculé à la demande.

    C'est le pull de secours du retour Checkout (§6.5) : le SPA revient avec
    `?billing=success` avant, peut-être, que le webhook ne soit arrivé, et sans
    cet appel l'utilisateur verrait « aucun abonnement » juste après avoir payé.
    """

    def get(self, request, app_slug, external_user_id):
        app = request.billing_app
        if app.slug != app_slug:
            # Une app signe pour elle-même, jamais pour une autre.
            return error("forbidden_app", "Cette app ne peut pas lire ce droit.", status.HTTP_403_FORBIDDEN)

        entitlement = Entitlement.objects.filter(app=app, external_user_id=external_user_id).first()
        if entitlement is None:
            entitlement = recompute_entitlement(app, external_user_id)
        return Response(entitlement.payload())


class CheckoutView(SignedServiceView):
    """Ouvre une session Stripe Checkout et renvoie son URL."""

    def post(self, request):
        app = request.billing_app
        stripe = stripe_client()
        if stripe is None:
            return error("billing_unconfigured", "Stripe n'est pas configuré.", status.HTTP_503_SERVICE_UNAVAILABLE)

        data = request.data
        external_user_id = str(data.get("external_user_id") or "")
        email = data.get("email") or ""
        plan_code = data.get("plan")
        interval = data.get("interval")
        quantity = data.get("quantity") or 1
        success_url = data.get("success_url") or ""
        cancel_url = data.get("cancel_url") or ""

        if not external_user_id:
            return error("missing_user", "external_user_id est requis.", status.HTTP_400_BAD_REQUEST)

        plan = Plan.objects.filter(app=app, code=plan_code, active=True).first()
        if plan is None:
            return error("unknown_plan", "Plan inconnu pour cette app.", status.HTTP_400_BAD_REQUEST)

        price = plan.price_for(interval)
        if price is None:
            return error("unknown_interval", "Aucun prix pour cet intervalle.", status.HTTP_400_BAD_REQUEST)

        if not (url_is_allowed_for(app, success_url) and url_is_allowed_for(app, cancel_url)):
            return error("bad_return_url", "URL de retour hors du domaine de l'app.", status.HTTP_400_BAD_REQUEST)

        customer = AppCustomer.objects.filter(app=app, external_user_id=external_user_id).first()
        if customer is None:
            customer = AppCustomer.objects.create(app=app, external_user_id=external_user_id, email=email)
        elif email and customer.email != email:
            customer.email = email
            customer.save(update_fields=["email"])

        params = {
            "mode": "subscription",
            # La quantite fixe le quota pour un plan facture a l'unite.
            "line_items": [{"price": price.id, "quantity": max(1, int(quantity))}],
            "client_reference_id": f"{app.slug}:{external_user_id}",
            "metadata": {
                "app": app.slug,
                "external_user_id": external_user_id,
                "plan": plan.code,
                "quantity": str(max(1, int(quantity))),
            },
            "success_url": success_url,
            "cancel_url": cancel_url,
            # TVA : Stripe calcule et collecte selon l'adresse, gère l'OSS et le
            # numéro de TVA en B2B (§17). À activer dès la première session.
            "automatic_tax": {"enabled": True},
            "customer_update": {"address": "auto"},
            "tax_id_collection": {"enabled": True},
        }
        essai = trial_days_for(stripe, plan, customer, quantity)
        if essai:
            params["subscription_data"] = {"trial_period_days": essai}
        elif plan.per_unit_quota_key:
            # Le client ajuste lui-meme le nombre d'exemplaires sur la page Stripe.
            # JAMAIS pendant un essai : l'essai n'est accorde que pour une quantite
            # de 1, et le laisser ajustable permettrait de le monter a 50 sur la
            # page de paiement — l'offrande deviendrait cinquante fois plus chere.
            params["line_items"][0]["adjustable_quantity"] = {
                "enabled": True,
                "minimum": 1,
                "maximum": MAX_UNITS_PER_CHECKOUT,
            }

        customer_id = known_stripe_customer_id(app, external_user_id)
        if customer_id:
            # Un client deja abonne qui repasse par Checkout obtiendrait un
            # SECOND abonnement Stripe : deux prelevements, et un droit qui
            # oscille entre les deux au gre des webhooks. Le refus vit ici plutot
            # que dans chaque front, parce que c'est la seule frontiere que
            # toutes les apps traversent -- y compris celles qui n'existent pas
            # encore. Changer de formule passe par le portail.
            if active_subscription_for(stripe, customer_id) is not None:
                return error(
                    "already_subscribed",
                    "Un abonnement est deja en cours pour ce compte.",
                    status.HTTP_409_CONFLICT,
                )
            # Reutiliser le client existant : sinon Stripe en cree un nouveau a
            # chaque paiement, les factures se dispersent entre plusieurs fiches
            # et le portail n'en montre qu'une.
            params["customer"] = customer_id
        elif email:
            params["customer_email"] = email

        session = stripe.checkout.Session.create(**params)
        return Response({"url": session.url})


class PortalView(SignedServiceView):
    """Ouvre le portail client Stripe et renvoie son URL."""

    def post(self, request):
        app = request.billing_app
        stripe = stripe_client()
        if stripe is None:
            return error("billing_unconfigured", "Stripe n'est pas configuré.", status.HTTP_503_SERVICE_UNAVAILABLE)

        external_user_id = str(request.data.get("external_user_id") or "")
        return_url = request.data.get("return_url") or ""

        if not url_is_allowed_for(app, return_url):
            return error("bad_return_url", "URL de retour hors du domaine de l'app.", status.HTTP_400_BAD_REQUEST)

        entitlement = Entitlement.objects.filter(app=app, external_user_id=external_user_id).first()
        customer_id = entitlement.stripe_customer_id if entitlement else ""
        if not customer_id:
            return error("no_customer", "Aucun abonnement à gérer.", status.HTTP_400_BAD_REQUEST)

        session = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
        return Response({"url": session.url})


class HistoryView(SignedServiceView):
    """Abonnements et factures d'un utilisateur, lus depuis le miroir dj-stripe.

    Volontairement servi depuis notre base et non depuis Stripe en direct : la page
    reste rapide, et s'affiche même si Stripe est brièvement injoignable.
    """

    def get(self, request):
        app = request.billing_app
        external_user_id = str(request.query_params.get("external_user_id") or "")

        # On passe par l'identifiant Stripe et non par `AppCustomer.customer` :
        # cette FK n'a jamais ete renseignee, si bien que l'historique renvoyait
        # systematiquement deux listes vides -- sans erreur, donc sans que rien
        # ne le signale.
        customer_id = known_stripe_customer_id(app, external_user_id)
        miroir = DjstripeCustomer.objects.filter(id=customer_id).first() if customer_id else None
        if miroir is None:
            return Response({"subscriptions": [], "invoices": []})

        subscriptions = [self._serialize_subscription(app, sub) for sub in miroir.subscriptions.all()]
        invoices = [
            {
                "id": inv.id,
                "number": inv.number or "",
                "status": inv.status,
                "amount_paid": inv.amount_paid,
                "currency": (inv.currency or "").upper(),
                "created": inv.created.isoformat() if inv.created else None,
                "hosted_invoice_url": inv.hosted_invoice_url or "",
                "invoice_pdf": inv.invoice_pdf or "",
            }
            for inv in miroir.invoices.all()
        ]
        return Response({"subscriptions": subscriptions, "invoices": invoices})

    @staticmethod
    def _serialize_subscription(app, sub):
        """Ce que l'utilisateur doit pouvoir relire d'un abonnement passé.

        Sans le plan, l'intervalle, la quantité et les dates, la page d'historique
        n'affiche qu'une colonne de tirets — on ne peut pas dire qu'on est
        transparent sur la facturation en montrant ça. Tout se lit dans
        `stripe_data` : en dj-stripe 2.11 la plupart des champs Stripe n'ont pas
        de colonne dédiée.
        """
        data = sub.stripe_data or {}
        plan, interval = plan_for_price(app, price_id_of(data))
        period_end = period_end_of(data)
        started = data.get("start_date")
        return {
            "id": sub.id,
            "status": sub.status,
            "plan": plan.code if plan else "",
            "plan_name": plan.name if plan else "",
            "interval": interval,
            "quantity": quantity_of(data),
            "started_at": (
                datetime.fromtimestamp(started, tz=get_current_timezone()).isoformat()
                if started
                else None
            ),
            "current_period_end": period_end.isoformat() if period_end else None,
            "canceled_at": sub.canceled_at.isoformat() if sub.canceled_at else None,
        }


class QuantityView(SignedServiceView):
    """Change le nombre d'exemplaires souscrits — après l'avoir annoncé.

    Deux temps volontairement séparés :
    - `POST /quantity/preview/` dit ce que ça coûtera, sans rien modifier ;
    - `POST /quantity/` applique.

    On ne modifie jamais un abonnement sans que le client ait pu voir le montant :
    le prorata d'un changement en cours de période n'est pas devinable, et une
    facture surprise est le meilleur moyen de perdre un client qui payait.
    """

    def post(self, request, action=None):
        app = request.billing_app
        stripe = stripe_client()
        if stripe is None:
            return error("billing_unconfigured", "Stripe n'est pas configuré.", status.HTTP_503_SERVICE_UNAVAILABLE)

        external_user_id = str(request.data.get("external_user_id") or "")
        try:
            quantity = int(request.data.get("quantity"))
        except (TypeError, ValueError):
            return error("bad_quantity", "quantity doit être un entier.", status.HTTP_400_BAD_REQUEST)
        if quantity < 1 or quantity > MAX_UNITS_PER_CHECKOUT:
            return error(
                "bad_quantity",
                f"quantity doit être compris entre 1 et {MAX_UNITS_PER_CHECKOUT}.",
                status.HTTP_400_BAD_REQUEST,
            )

        entitlement = Entitlement.objects.filter(app=app, external_user_id=external_user_id).first()
        customer_id = entitlement.stripe_customer_id if entitlement else ""
        subscription = active_subscription_for(stripe, customer_id)
        if subscription is None:
            return error("no_subscription", "Aucun abonnement à modifier.", status.HTTP_400_BAD_REQUEST)

        if action == "preview":
            return Response(preview_quantity_change(stripe, subscription, quantity))

        apply_quantity_change(stripe, subscription, quantity)
        # Le webhook customer.subscription.updated recalculera et poussera le droit ;
        # on ne duplique pas ce calcul ici pour éviter deux vérités concurrentes.
        logger.info(
            "quantity_changed",
            extra={"app": app.slug, "user": external_user_id, "quantity": quantity},
        )
        return Response({"quantity": quantity, "status": "applied"})
