from .base import *

DEBUG = False

# nginx termine le TLS (OPERATIONS.md §3.6).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS et nosniff : posés par nginx SEULEMENT (deploy/nginx/billing-api.conf).
# Les deux couches les envoyaient, avec des max-age différents (audit
# 2026-10-18, M10). nginx couvre aussi /static/ et ses pages d'erreur, que
# Django ne voit pas. Ne pas réactiver ici sans retirer les add_header du vhost.
SECURE_HSTS_SECONDS = 0
SECURE_CONTENT_TYPE_NOSNIFF = False
# `check --deploy` ne voit pas nginx : W004 (HSTS) et W006 (nosniff) sont faux ici.
SILENCED_SYSTEM_CHECKS = ["security.W004", "security.W006"]
