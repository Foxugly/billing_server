"""Garde-fous de configuration, exécutés par `manage.py check`.

Le déploiement lance `migrate` et `collectstatic`, qui exécutent tous deux les
system checks : une erreur ici **fait échouer le déploiement**. C'est voulu. Le
bug qui a motivé ce module ne plantait rien — `REDIS_URL` absent en production
faisait silencieusement retomber Celery sur un broker mémoire et le cache sur
LocMem, avec un worker qui tournait dans le vide et un anti-rejeu par processus.
Tout paraissait vert.
"""
from django.conf import settings
from django.core.checks import Error, register


@register()
def redis_is_configured_in_production(app_configs, **kwargs):
    if getattr(settings, "STATE", "DEV") != "PROD":
        return []

    problems = []

    if not getattr(settings, "REDIS_URL", ""):
        problems.append(
            Error(
                "REDIS_URL est absent alors que STATE=PROD.",
                hint=(
                    "Sans lui, CELERY_BROKER_URL retombe sur memory://, "
                    "CELERY_TASK_ALWAYS_EAGER passe à True et le cache devient LocMem : "
                    "le worker Celery tourne dans le vide et l'anti-rejeu des signatures "
                    "devient par processus. Seeder /billing/prod/REDIS_URL "
                    "(redis://127.0.0.1:6379/4), puis redémarrer billing-env-fetch."
                ),
                id="billing.E001",
            )
        )

    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        problems.append(
            Error(
                "CELERY_TASK_ALWAYS_EAGER est actif alors que STATE=PROD.",
                hint="Les tâches s'exécuteraient dans le processus web, sans reprise possible.",
                id="billing.E002",
            )
        )

    locmem = "locmem" in settings.CACHES["default"]["BACKEND"].lower()
    if locmem:
        problems.append(
            Error(
                "Le cache est LocMem alors que STATE=PROD.",
                hint=(
                    "L'anti-rejeu des signatures HMAC s'appuie sur le cache : avec LocMem, "
                    "chaque worker gunicorn a le sien et un rejeu passe une fois par worker."
                ),
                id="billing.E003",
            )
        )

    return problems
