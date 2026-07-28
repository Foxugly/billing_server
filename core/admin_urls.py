from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .admin_views import (
    AppViewSet,
    CustomerViewSet,
    DashboardView,
    DeliveryViewSet,
    EntitlementViewSet,
    PlanViewSet,
)


router = DefaultRouter()
router.register("apps", AppViewSet, basename="admin-app")
router.register("plans", PlanViewSet, basename="admin-plan")
router.register("customers", CustomerViewSet, basename="admin-customer")
router.register("entitlements", EntitlementViewSet, basename="admin-entitlement")
router.register("deliveries", DeliveryViewSet, basename="admin-delivery")

app_name = "billing_admin"

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("", include(router.urls)),
]
