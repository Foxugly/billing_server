from django.urls import path

from .api_views import (
    CookieTokenBlacklistView,
    CookieTokenRefreshView,
    MeView,
    PasswordChangeView,
    StaffTokenObtainPairView,
)

urlpatterns = [
    path("token/", StaffTokenObtainPairView.as_view(), name="token-obtain"),
    path("token/refresh/", CookieTokenRefreshView.as_view(), name="token-refresh"),
    # Logout serveur : le refresh présenté (corps, ou cookie HttpOnly en mode
    # `X-Auth-Mode: cookie`) ne pourra plus servir.
    path("token/blacklist/", CookieTokenBlacklistView.as_view(), name="token-blacklist"),
    path("me/", MeView.as_view(), name="me"),
    path("password/change/", PasswordChangeView.as_view(), name="password-change"),
]
