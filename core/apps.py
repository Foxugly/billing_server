from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Importe le module pour que les décorateurs @djstripe_receiver s'enregistrent.
        from . import webhooks  # noqa: F401
