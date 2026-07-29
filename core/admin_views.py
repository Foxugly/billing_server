"""API de la console d'exploitation — réservée aux opérateurs (`is_staff`).

Rien ici n'est atteignable par une application de la flotte : l'API
service-à-service (`/api/v1/`) et cette API-ci (`/api/v1/admin/`) ont des
authentifications entièrement disjointes — signature HMAC d'un côté, JWT
d'opérateur de l'autre. Une app compromise ne peut donc pas lire la facturation
des autres sites.
"""
import csv
import logging
from datetime import datetime, time

from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .admin_serializers import (
    AppCustomerSerializer,
    AppSerializer,
    EntitlementDeliverySerializer,
    EntitlementSerializer,
    EventSerializer,
    InvoiceDraftSerializer,
    InvoiceSerializer,
    PlanSerializer,
    PriceSerializer,
)
from .invoicing import (
    InvoicingError,
    create_draft_invoice,
    ensure_direct_customer,
    finalize_invoice,
    mark_paid_out_of_band,
    send_invoice,
    tax_codes,
    void_invoice,
)
from .models import App, AppCustomer, Entitlement, EntitlementDelivery, Plan
from .stripe_gateway import stripe_configured

logger = logging.getLogger("billing")

# Un mois « normalisé » pour comparer mensuel et annuel sur la même échelle.
MONTHS_PER_YEAR = 12


class StaffViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAdminUser]


class AppViewSet(StaffViewSet):
    queryset = App.objects.all()
    serializer_class = AppSerializer

    @action(detail=True, methods=["post"])
    def rotate_secret(self, request, pk=None):
        """Génère un nouveau secret. L'ancien reste accepté 24 h (§8).

        Le nouveau secret est renvoyé **une seule fois**, ici : il n'est jamais
        relisible ensuite. C'est le seul moment où l'opérateur peut le copier
        vers le SSM de l'application.
        """
        app = self.get_object()
        secret = app.rotate_secret()
        logger.info("app_secret_rotated", extra={"app": app.slug})
        return Response(
            {
                "slug": app.slug,
                "shared_secret": secret,
                "rotated_at": app.secret_rotated_at,
                "warning": (
                    "Ce secret n'est affiché qu'une fois. L'ancien reste accepté 24 h : "
                    "poser le nouveau dans le SSM de l'application avant ce délai."
                ),
            }
        )

    @action(detail=True, methods=["post"])
    def ping(self, request, pk=None):
        """Teste la connectivité et le secret partagé, côté serveur.

        Le test part du serveur et non du navigateur : c'est le serveur qui
        détient le secret, et il ne doit jamais atteindre un bundle SPA.
        """
        from .tasks import ping_app

        ok, detail = ping_app(self.get_object())
        return Response({"ok": ok, "detail": detail})


class PlanViewSet(StaffViewSet):
    queryset = Plan.objects.select_related("app", "price_monthly", "price_yearly")
    serializer_class = PlanSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        app_slug = self.request.query_params.get("app")
        return queryset.filter(app__slug=app_slug) if app_slug else queryset


class EventViewSet(viewsets.ViewSet):
    """Les événements Stripe reçus, et leur rejeu.

    Le rejeu réémet le signal dj-stripe : notre récepteur repasse dessus et
    recalcule le droit. Le vrai usage est un bug de traitement corrigé — on
    repasse les événements qui en ont souffert sans rien redemander à Stripe.
    Sans effet de bord fâcheux : le recalcul est idempotent.
    """

    permission_classes = [permissions.IsAdminUser]

    def _evenements(self):
        from djstripe.models import Event

        return Event.objects.order_by("-created")

    def list(self, request):
        from .webhooks import SUBSCRIPTION_EVENTS

        evenements = self._evenements()
        if motif := request.query_params.get("type"):
            evenements = evenements.filter(type__icontains=motif)
        if request.query_params.get("handled") == "true":
            evenements = evenements.filter(type__in=SUBSCRIPTION_EVENTS)
        return Response(EventSerializer(list(evenements), many=True).data)

    @action(detail=True, methods=["post"])
    def replay(self, request, pk=None):
        from djstripe.models import Event

        from .webhooks import SUBSCRIPTION_EVENTS, handle_subscription_event

        # `id` et non `pk` : la clé primaire de dj-stripe est un entier interne
        # (`djstripe_id`), l'identifiant Stripe `evt_…` vit dans `id`.
        evenement = Event.objects.filter(id=pk).first()
        if evenement is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if evenement.type not in SUBSCRIPTION_EVENTS:
            # Réémettre le signal ne ferait rien : autant le dire plutôt que de
            # laisser croire à un rejeu réussi.
            return Response(
                {"detail": f"Aucun traitement n'est associé au type « {evenement.type} »."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # On appelle le traitement directement, sans passer par le signal : le
        # signal est le chemin de Stripe, avec sa vérification de signature et sa
        # mise à jour du miroir, déjà faites la première fois.
        livraison = handle_subscription_event(
            (evenement.data or {}).get("object") if evenement.data else None
        )
        logger.info("event_replayed", extra={"event": evenement.id, "type": evenement.type})
        return Response(
            {
                "id": evenement.id,
                "type": evenement.type,
                "delivery": str(livraison.pk) if livraison else None,
                "detail": (
                    "Droit recalculé et livraison mise en file."
                    if livraison
                    else "Événement non attribuable à une app : rien à recalculer."
                ),
            }
        )


class PriceViewSet(viewsets.ViewSet):
    """Les prix Stripe mirés, pour le sélecteur de la page Plans.

    En lecture seule : un prix ne se crée ni ne se modifie ici. Stripe les rend
    immuables sur les champs qui comptent (montant, `tax_behavior`) — un
    changement de tarif se fait en créant un nouveau prix côté Stripe, puis en
    recâblant le plan dessus.
    """

    permission_classes = [permissions.IsAdminUser]

    def list(self, request):
        from djstripe.models import Price

        # Les prix archivés sont exclus : Stripe refuse de vendre dessus, les
        # proposer donnerait un plan invendable.
        prix = Price.objects.filter(active=True).select_related("product").order_by("id")
        return Response(PriceSerializer(list(prix), many=True).data)


class CustomerViewSet(StaffViewSet):
    queryset = AppCustomer.objects.select_related("app", "customer")
    serializer_class = AppCustomerSerializer
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        if params.get("app"):
            queryset = queryset.filter(app__slug=params["app"])
        if params.get("email"):
            queryset = queryset.filter(email__icontains=params["email"])
        return queryset


class EntitlementViewSet(StaffViewSet):
    queryset = Entitlement.objects.select_related("app")
    serializer_class = EntitlementSerializer
    http_method_names = ["get", "head", "options", "post"]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        if params.get("app"):
            queryset = queryset.filter(app__slug=params["app"])
        if params.get("user"):
            queryset = queryset.filter(external_user_id=params["user"])
        return queryset

    @action(detail=True, methods=["post"])
    def grant(self, request, pk=None):
        """Offre l'accès : passe l'entitlement en `source=manual`.

        C'est la seule façon correcte d'ouvrir un accès à la main. Éditer les
        champs dérivés créerait un état que le prochain webhook écraserait sans
        prévenir ; `manual` est au contraire respecté par tout recalcul.
        """
        entitlement = self.get_object()
        entitlement.source = Entitlement.MANUAL
        entitlement.is_paid = True
        quotas = request.data.get("quotas")
        if isinstance(quotas, dict):
            entitlement.quotas = quotas
        entitlement.save()

        delivery = EntitlementDelivery.objects.create(
            entitlement=entitlement, payload=entitlement.payload()
        )
        from .tasks import deliver_entitlement

        deliver_entitlement.delay(str(delivery.pk))
        logger.info("entitlement_granted", extra={"app": entitlement.app.slug, "user": entitlement.external_user_id})
        return Response(EntitlementSerializer(entitlement).data)


class DeliveryViewSet(StaffViewSet):
    queryset = EntitlementDelivery.objects.select_related("entitlement__app")
    serializer_class = EntitlementDeliverySerializer
    http_method_names = ["get", "head", "options", "post"]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        if params.get("app"):
            queryset = queryset.filter(entitlement__app__slug=params["app"])
        return queryset

    @action(detail=True, methods=["post"])
    def replay(self, request, pk=None):
        """Renvoie une livraison en échec. Idempotent côté application."""
        delivery = self.get_object()
        delivery.status = EntitlementDelivery.PENDING
        delivery.next_retry_at = None
        delivery.save(update_fields=["status", "next_retry_at"])

        from .tasks import deliver_entitlement

        deliver_entitlement.delay(str(delivery.pk))
        return Response({"id": str(delivery.pk), "status": delivery.status})


class DashboardView(APIView):
    """Chiffres d'exploitation : ce qui rentre, et ce qui ne passe pas."""

    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        apps = App.objects.annotate(
            paid=Count("entitlements", filter=Q(entitlements__is_paid=True)),
            total=Count("entitlements", distinct=True),
        )

        mrr_cents = 0
        for entitlement in Entitlement.objects.filter(is_paid=True).select_related("app"):
            plan = Plan.objects.filter(app=entitlement.app, code=entitlement.plan_code).first()
            if plan is None:
                continue
            price = plan.price_for(entitlement.interval)
            if price is None or price.unit_amount is None:
                continue
            # Un abonnement annuel compte pour un douzième de son montant :
            # sans cette normalisation, le MRR sauterait au rythme des annuels.
            mrr_cents += (
                price.unit_amount // MONTHS_PER_YEAR
                if entitlement.interval == Plan.YEARLY
                else price.unit_amount
            )

        return Response(
            {
                "mrr_cents": mrr_cents,
                "apps": [
                    {"slug": a.slug, "name": a.name, "active": a.active, "paid": a.paid, "total": a.total}
                    for a in apps
                ],
                "deliveries": {
                    "pending": EntitlementDelivery.objects.filter(
                        status=EntitlementDelivery.PENDING
                    ).count(),
                    "failed": EntitlementDelivery.objects.filter(
                        status=EntitlementDelivery.FAILED
                    ).count(),
                },
                "customers": AppCustomer.objects.count(),
            }
        )


# --------------------------------------------------------------- facturation directe

# Ce qu'un comptable attend d'un export : de quoi rapprocher sans ouvrir un PDF.
COLONNES_EXPORT = [
    "date", "numero", "client", "numero_tva", "ht", "tva", "ttc", "devise", "statut", "origine",
]


def _euros(centimes) -> str:
    """Un export comptable se lit en euros. Les centimes sont une unite d'API."""
    return f"{(centimes or 0) / 100:.2f}"


def _borne(valeur: str, fin: bool):
    """Convertit une date `YYYY-MM-DD` de l'URL en instant conscient du fuseau."""
    if not valeur:
        return None
    jour = datetime.strptime(valeur, "%Y-%m-%d").date()
    limite = time.max if fin else time.min
    return timezone.make_aware(datetime.combine(jour, limite))


class InvoiceViewSet(viewsets.ViewSet):
    """Emission et suivi des factures de prestation (§16).

    Les objets vivent chez Stripe ; ce ViewSet pilote leur cycle de vie et lit le
    miroir dj-stripe. D'ou un `ViewSet` nu plutot qu'un `ModelViewSet` : il n'y a
    pas de modele local a mettre a jour, et un `PATCH` sur une facture finalisee
    n'aurait aucun sens.
    """

    permission_classes = [permissions.IsAdminUser]

    def _factures(self):
        from djstripe.models import Invoice

        return Invoice.objects.select_related("customer").order_by("-created")

    def list(self, request):
        factures = self._factures()
        origine = request.query_params.get("origin")
        if origine == "direct":
            factures = [f for f in factures if (f.metadata or {}).get("origin") == "direct"]
        return Response(InvoiceSerializer(list(factures), many=True).data)

    def create(self, request):
        if not stripe_configured():
            return Response(
                {"detail": "Stripe n'est pas configure : les cles arrivent au lot L6."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        formulaire = InvoiceDraftSerializer(data=request.data)
        formulaire.is_valid(raise_exception=True)
        donnees = formulaire.validated_data

        try:
            client = ensure_direct_customer(
                email=donnees["customer"]["email"],
                name=donnees["customer"].get("name", ""),
                address=donnees["customer"].get("address"),
            )
            facture = create_draft_invoice(
                client,
                donnees["lines"],
                days_until_due=donnees["days_until_due"],
                description=donnees.get("description", ""),
            )
        except InvoicingError as refus:
            # Une faute de saisie doit revenir dans le formulaire, pas en 500.
            return Response({"detail": str(refus)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(InvoiceSerializer(facture).data, status=status.HTTP_201_CREATED)

    def _etape(self, fonction, invoice_id):
        try:
            facture = fonction(invoice_id)
        except InvoicingError as refus:
            return Response({"detail": str(refus)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InvoiceSerializer(facture).data)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        """Fige la facture et lui attribue son numero. Irreversible."""
        return self._etape(finalize_invoice, pk)

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        return self._etape(send_invoice, pk)

    @action(detail=True, methods=["post"], url_path="mark_paid")
    def mark_paid(self, request, pk=None):
        """Solde une facture reglee par virement, hors Stripe."""
        return self._etape(mark_paid_out_of_band, pk)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        """Annule une facture emise. Une piece comptable ne se supprime pas."""
        return self._etape(void_invoice, pk)

    @action(detail=False, methods=["get"])
    def export(self, request):
        """Export CSV d'une periode, pour le comptable.

        Les brouillons en sont exclus : sans numero, ils ne sont pas des pieces
        comptables, et les faire figurer dans un journal de ventes serait faux.
        """
        debut = _borne(request.query_params.get("from", ""), fin=False)
        fin = _borne(request.query_params.get("to", ""), fin=True)

        reponse = HttpResponse(content_type="text/csv; charset=utf-8")
        reponse["Content-Disposition"] = 'attachment; filename="factures.csv"'
        # BOM : sans lui, Excel ouvre l'UTF-8 en latin-1 et massacre les accents.
        reponse.write("﻿")
        # Point-virgule : separateur attendu par Excel en locale francophone.
        journal = csv.writer(reponse, delimiter=";")
        journal.writerow(COLONNES_EXPORT)

        serialiseur = InvoiceSerializer()
        for facture in self._factures():
            if facture.status == "draft" or not facture.number:
                continue
            emise_le = facture.created
            # Une facture sans date ne peut pas etre rangee dans une periode :
            # l'exclure vaut mieux que la faire tomber dans le mauvais exercice.
            if (debut or fin) and emise_le is None:
                continue
            if debut and emise_le < debut:
                continue
            if fin and emise_le > fin:
                continue

            tva = facture.stripe_data.get("customer_tax_ids") or []
            journal.writerow([
                emise_le.date().isoformat() if emise_le else "",
                facture.number,
                serialiseur.get_customer_email(facture),
                tva[0].get("value", "") if tva else "",
                _euros(facture.subtotal),
                _euros(facture.tax),
                _euros(facture.total),
                (facture.currency or "").upper(),
                facture.status,
                serialiseur.get_origin(facture),
            ])

        return reponse


class TaxCodeView(APIView):
    """Le catalogue des codes fiscaux Stripe, pour le selecteur du formulaire."""

    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        try:
            return Response(tax_codes(request.query_params.get("q", "")))
        except InvoicingError as refus:
            return Response({"detail": str(refus)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
