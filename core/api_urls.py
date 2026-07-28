from django.urls import path

from .api_views import (
    CheckoutView,
    EntitlementView,
    HistoryView,
    PingView,
    PlansView,
    PortalView,
    QuantityView,
)


app_name = "billing_api"

urlpatterns = [
    path("ping/", PingView.as_view(), name="ping"),
    path("plans/", PlansView.as_view(), name="plans"),
    path("checkout/", CheckoutView.as_view(), name="checkout"),
    path("portal/", PortalView.as_view(), name="portal"),
    path("history/", HistoryView.as_view(), name="history"),
    path("quantity/preview/", QuantityView.as_view(), {"action": "preview"}, name="quantity-preview"),
    path("quantity/", QuantityView.as_view(), name="quantity"),
    path(
        "entitlements/<slug:app_slug>/<str:external_user_id>/",
        EntitlementView.as_view(),
        name="entitlement",
    ),
]
