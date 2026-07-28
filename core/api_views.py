"""API service-à-service. Toutes les vues exigent une signature HMAC (§8).

Aucune de ces routes n'est atteignable depuis un navigateur : elles sont appelées
par les backends de la flotte, jamais par un SPA. C'est le backend de l'app qui
connaît l'identité de son utilisateur — le central ne fait que lui faire confiance,
signature à l'appui.
"""
import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AppCustomer, Entitlement, Plan
from .permissions import HasValidAppSignature
from .services import recompute_entitlement
from .stripe_gateway import stripe_client, stripe_configured, url_is_allowed_for

logger = logging.getLogger("billing")


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
            "line_items": [{"price": price.id, "quantity": 1}],
            "client_reference_id": f"{app.slug}:{external_user_id}",
            "metadata": {
                "app": app.slug,
                "external_user_id": external_user_id,
                "plan": plan.code,
            },
            "success_url": success_url,
            "cancel_url": cancel_url,
            # TVA : Stripe calcule et collecte selon l'adresse, gère l'OSS et le
            # numéro de TVA en B2B (§17). À activer dès la première session.
            "automatic_tax": {"enabled": True},
            "customer_update": {"address": "auto"},
            "tax_id_collection": {"enabled": True},
        }
        if customer.customer_id:
            params["customer"] = customer.customer_id
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

        customer = AppCustomer.objects.filter(
            app=app, external_user_id=external_user_id
        ).select_related("customer").first()

        if customer is None or customer.customer is None:
            return Response({"subscriptions": [], "invoices": []})

        subscriptions = [
            {
                "id": sub.id,
                "status": sub.status,
                "current_period_end": None,
                "canceled_at": sub.canceled_at.isoformat() if sub.canceled_at else None,
            }
            for sub in customer.customer.subscriptions.all()
        ]
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
            for inv in customer.customer.invoices.all()
        ]
        return Response({"subscriptions": subscriptions, "invoices": invoices})
