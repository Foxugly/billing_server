"""API de la console d'exploitation — réservée aux opérateurs (`is_staff`).

Rien ici n'est atteignable par une application de la flotte : l'API
service-à-service (`/api/v1/`) et cette API-ci (`/api/admin/`) ont des
authentifications entièrement disjointes — signature HMAC d'un côté, JWT
d'opérateur de l'autre. Une app compromise ne peut donc pas lire la facturation
des autres sites.
"""
import logging

from django.db.models import Count, Q
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .admin_serializers import (
    AppCustomerSerializer,
    AppSerializer,
    EntitlementDeliverySerializer,
    EntitlementSerializer,
    PlanSerializer,
)
from .models import App, AppCustomer, Entitlement, EntitlementDelivery, Plan

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
