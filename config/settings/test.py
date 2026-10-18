from .base import *

DEBUG = False

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Hôtes non routables (RFC 2606) : aucune fixture ne porte le domaine de prod.
BILLING_APP_HOST_SUFFIXES = [".foxugly.invalid"]
