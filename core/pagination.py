"""Pagination des listes de la console (audit 2026-10-18, M5).

Avant, `EventViewSet.list` sérialisait **tous** les événements Stripe en une
réponse : le compte est partagé par toute la flotte, la table ne fait que
grossir. Les apps signées ne sont pas concernées : leurs vues sont des `APIView`
qui renvoient leur propre forme.
"""
from rest_framework.pagination import PageNumberPagination


class ConsolePagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


def paginated(view, request, rows, serializer_class):
    """Pagine une liste hors queryset de modèle (miroir dj-stripe, liste filtrée
    en Python) avec la même enveloppe que les ModelViewSet."""
    paginator = ConsolePagination()
    page = paginator.paginate_queryset(rows, request, view=view)
    return paginator.get_paginated_response(serializer_class(page, many=True).data)
