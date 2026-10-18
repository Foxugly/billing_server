"""Une seule source pour HSTS et nosniff : nginx (audit 2026-10-18, M10).

En live, `billing-api` renvoyait chaque en-tête deux fois — nginx
(`max-age=63072000`) et la SecurityMiddleware de Django (`max-age=31536000;
preload`). Deux valeurs contradictoires : un navigateur n'en retient qu'une, et
laquelle n'est pas spécifiée. nginx couvre aussi /static/ et ses propres pages
d'erreur (502 quand gunicorn redémarre), que Django ne voit jamais.
"""
import importlib
from pathlib import Path

VHOST = Path(__file__).resolve().parents[2] / "deploy" / "nginx" / "billing-api.conf"


def test_nginx_sets_hsts_and_nosniff_once():
    conf = VHOST.read_text(encoding="utf-8")
    assert conf.count('add_header Strict-Transport-Security') == 1
    assert conf.count('add_header X-Content-Type-Options') == 1


def test_django_does_not_emit_them_in_production():
    prod = importlib.import_module("config.settings.prod")

    assert prod.SECURE_HSTS_SECONDS == 0
    assert prod.SECURE_CONTENT_TYPE_NOSNIFF is False
