from django.urls import path
from rest_framework_simplejwt.views import TokenBlacklistView, TokenRefreshView

from .api_views import MeView, PasswordChangeView, StaffTokenObtainPairView

urlpatterns = [
    path("token/", StaffTokenObtainPairView.as_view(), name="token-obtain"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    # Logout serveur : le refresh présenté ne pourra plus servir.
    path("token/blacklist/", TokenBlacklistView.as_view(), name="token-blacklist"),
    path("me/", MeView.as_view(), name="me"),
    path("password/change/", PasswordChangeView.as_view(), name="password-change"),
]
