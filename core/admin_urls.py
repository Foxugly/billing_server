from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .admin_views import (
    AppViewSet,
    CustomerViewSet,
    DashboardView,
    DeliveryViewSet,
    EntitlementViewSet,
    EventViewSet,
    InvoiceViewSet,
    PlanViewSet,
    PriceViewSet,
    TaxCodeView,
)


router = DefaultRouter()
router.register("apps", AppViewSet, basename="admin-app")
router.register("plans", PlanViewSet, basename="admin-plan")
router.register("prices", PriceViewSet, basename="admin-price")
router.register("customers", CustomerViewSet, basename="admin-customer")
router.register("entitlements", EntitlementViewSet, basename="admin-entitlement")
router.register("deliveries", DeliveryViewSet, basename="admin-delivery")
router.register("invoices", InvoiceViewSet, basename="admin-invoice")
router.register("events", EventViewSet, basename="admin-event")

app_name = "billing_admin"

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("tax-codes/", TaxCodeView.as_view(), name="tax-codes"),
    path("", include(router.urls)),
]
