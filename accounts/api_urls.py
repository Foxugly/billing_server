from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .api_views import MeView, StaffTokenObtainPairView


urlpatterns = [
    path("token/", StaffTokenObtainPairView.as_view(), name="token-obtain"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("me/", MeView.as_view(), name="me"),
]
