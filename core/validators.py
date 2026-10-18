"""Validation des hôtes de retour d'une app (audit 2026-10-18, M2).

Partagée par le modèle (donc l'admin Django, qui édite le JSON brut) et le
sérialiseur de la console.
"""
import re

from django.conf import settings
from django.core.exceptions import ValidationError

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")


def clean_host(raw) -> str:
    return str(raw or "").strip().lower()


def normalize_return_hosts(value) -> list[str]:
    """Liste de noms d'hôte nus de la flotte, en minuscules, sans doublon.

    Ni schéma, ni chemin, ni port, ni joker : un joker rouvrirait exactement ce
    que l'audit M2 a fermé.
    """
    if not isinstance(value, list):
        raise ValidationError("Liste de noms d'hôte attendue, ex. [\"poker.foxugly.com\"].")
    suffixes = tuple(settings.BILLING_APP_HOST_SUFFIXES)
    hosts = []
    for raw in value:
        host = clean_host(raw)
        if not _HOSTNAME.match(host) or not host.endswith(suffixes):
            raise ValidationError(
                f"« {raw} » : nom d'hôte nu attendu (sans https:// ni chemin), "
                f"se terminant par {' ou '.join(suffixes)}."
            )
        if host not in hosts:
            hosts.append(host)
    return hosts


def validate_return_hosts(value):
    normalize_return_hosts(value)
