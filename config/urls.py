from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", include("health.urls")),
    # dj-stripe expose ses propres endpoints webhook : l'URL porte un UUID généré
    # (non devinable de l'extérieur) et chaque endpoint a son propre secret. Ils se
    # créent depuis l'admin (dj-stripe >= 2.7) — pas de vue webhook maison.
    path("stripe/", include("djstripe.urls", namespace="djstripe")),
]
