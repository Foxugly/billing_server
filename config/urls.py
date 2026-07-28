from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", include("health.urls")),
    # Deux APIs volontairement disjointes : /api/v1/ (racine) est signée en HMAC
    # et réservée aux apps de la flotte ; /api/v1/admin/ est en JWT et réservée
    # aux opérateurs. Une app compromise ne peut pas lire la facturation des
    # autres. Les plus spécifiques d'abord : la racine ne doit pas les avaler.
    path("api/v1/auth/", include("accounts.api_urls")),
    path("api/v1/admin/", include("core.admin_urls")),
    path("api/v1/", include("core.api_urls")),
    # dj-stripe expose ses propres endpoints webhook : l'URL porte un UUID généré
    # (non devinable de l'extérieur) et chaque endpoint a son propre secret. Ils se
    # créent depuis l'admin (dj-stripe >= 2.7) — pas de vue webhook maison.
    path("stripe/", include("djstripe.urls", namespace="djstripe")),
]
