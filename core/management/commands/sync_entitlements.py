from django.core.management.base import BaseCommand

from core.reconcile import reconcile


class Command(BaseCommand):
    help = "Recalcule les droits et, avec --push-diff, repousse ceux qui ont changé."

    def add_arguments(self, parser):
        parser.add_argument("--app", dest="app_slug", default=None, help="Limiter à une app.")
        parser.add_argument(
            "--push-diff",
            action="store_true",
            help="Émettre une livraison pour chaque droit qui a changé. Sans ce drapeau, "
            "la commande ne fait que constater — c'est le mode par défaut, volontairement.",
        )

    def handle(self, *args, **options):
        examined, changed, pushed = reconcile(
            app_slug=options["app_slug"], push_diff=options["push_diff"]
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"{examined} droit(s) examiné(s), {changed} modifié(s), {pushed} poussé(s)."
            )
        )
