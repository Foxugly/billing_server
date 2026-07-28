"""Modèles métier du service de facturation.

dj-stripe fournit le miroir des objets Stripe (Customer, Price, Subscription,
Invoice…) : on ne le modifie jamais. Ces modèles-ci sont la couche au-dessus —
qui est le client, ce qu'il a acheté, et quel droit en découle.

Le catalogue (App/Plan) appartient à CE service, pas aux applications de la flotte :
elles le lisent, elles ne l'écrivent jamais. Un tarif engage l'entreprise, et un
prix Stripe est immuable — ce n'est pas une donnée qu'on modifie depuis sept bases
de code différentes.
"""
import secrets
import uuid

from django.db import models
from django.utils import timezone


def generate_shared_secret() -> str:
    """Secret HMAC pour la signature service-à-service (§8)."""
    return secrets.token_urlsafe(48)


class App(models.Model):
    """Une application de la flotte qui délègue sa facturation à ce service."""

    slug = models.SlugField(unique=True, help_text="poker, tm, pushit…")
    name = models.CharField(max_length=100)
    base_url = models.URLField(help_text="Racine de l'API de l'app, ex. https://poker-api.foxugly.com")
    entitlement_path = models.CharField(
        max_length=200,
        default="/api/billing/entitlement/",
        help_text="Chemin du endpoint qui reçoit les droits poussés.",
    )
    shared_secret = models.CharField(max_length=100, default=generate_shared_secret)
    previous_shared_secret = models.CharField(
        max_length=100, blank=True, help_text="Accepté pendant 24 h après une rotation."
    )
    secret_rotated_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True, help_text="Décoché : plus aucune livraison n'est émise.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("slug",)

    def __str__(self):
        return self.slug

    def rotate_secret(self) -> str:
        """Génère un nouveau secret et conserve l'ancien le temps que l'app suive.

        Sans ce sursis, la rotation couperait le service entre l'instant où le
        central change de secret et celui où l'application redéploie le sien.
        """
        self.previous_shared_secret = self.shared_secret
        self.shared_secret = generate_shared_secret()
        self.secret_rotated_at = timezone.now()
        self.save(update_fields=["shared_secret", "previous_shared_secret", "secret_rotated_at"])
        return self.shared_secret

    @property
    def entitlement_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/{self.entitlement_path.lstrip('/')}"


class Plan(models.Model):
    """Une offre d'une app, adossée à un ou deux prix Stripe.

    `name` et `description` sont des libellés **internes**, pour l'admin. Ce que voit
    le client est rendu par l'application, dans sa langue et son ton : billing détient
    la vérité commerciale (code, prix, quotas), l'app détient la présentation.
    """

    MONTHLY = "monthly"
    YEARLY = "yearly"

    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="plans")
    code = models.CharField(max_length=50, help_text="team1, team5… l'identifiant utilisé par l'app")
    name = models.CharField(max_length=100, help_text="Libellé interne (admin), pas la vitrine client.")
    description = models.TextField(blank=True)
    price_monthly = models.ForeignKey(
        "djstripe.Price", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    price_yearly = models.ForeignKey(
        "djstripe.Price", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    quotas = models.JSONField(default=dict, help_text='Poussé tel quel à l\'app, ex. {"teams": 1}')
    sort_order = models.PositiveIntegerField(default=0)
    public = models.BooleanField(default=True, help_text="Décoché : masqué du catalogue de l'app.")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("app", "sort_order", "code")
        constraints = [models.UniqueConstraint(fields=("app", "code"), name="unique_plan_code_per_app")]

    def __str__(self):
        return f"{self.app.slug}/{self.code}"

    def price_for(self, interval: str):
        """Le Price Stripe pour un intervalle, ou None si non configuré."""
        return {self.MONTHLY: self.price_monthly, self.YEARLY: self.price_yearly}.get(interval)


class AppCustomer(models.Model):
    """Le pont entre « l'utilisateur 42 de poker » et un Customer Stripe.

    `app=NULL` désigne un **client direct** (prestation de consulting, §16) : aucun
    Entitlement n'est calculé pour lui et aucune livraison n'est émise. La colonne est
    nullable dès la première migration — l'ajouter après coup imposerait une migration
    sur une table déjà peuplée en production.
    """

    app = models.ForeignKey(
        App, null=True, blank=True, on_delete=models.CASCADE, related_name="customers"
    )
    external_user_id = models.CharField(max_length=100, blank=True)
    email = models.EmailField()
    customer = models.ForeignKey(
        "djstripe.Customer", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("email",)
        constraints = [
            models.UniqueConstraint(
                fields=("app", "external_user_id"), name="unique_customer_per_app_user"
            )
        ]

    def __str__(self):
        return f"{self.app.slug if self.app else 'direct'}:{self.external_user_id or self.email}"

    @property
    def is_direct(self) -> bool:
        return self.app_id is None


class Entitlement(models.Model):
    """L'état de droit dérivé, par (app, utilisateur). C'est le seul objet poussé."""

    STRIPE = "stripe"
    MANUAL = "manual"
    BYPASS = "bypass"
    SOURCES = [(STRIPE, "Stripe"), (MANUAL, "Manuel"), (BYPASS, "Bypass")]

    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="entitlements")
    external_user_id = models.CharField(max_length=100)
    is_paid = models.BooleanField(default=False)
    status = models.CharField(max_length=32, blank=True, help_text="Statut Stripe brut")
    plan_code = models.CharField(max_length=50, blank=True)
    interval = models.CharField(max_length=16, blank=True)
    quotas = models.JSONField(default=dict)
    current_period_end = models.DateTimeField(null=True, blank=True)
    grace_until = models.DateTimeField(
        null=True, blank=True, help_text="Fin de la période de grâce après un échec de paiement (§6.6)"
    )
    stripe_customer_id = models.CharField(max_length=64, blank=True)
    source = models.CharField(max_length=16, choices=SOURCES, default=STRIPE)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("app", "external_user_id")
        constraints = [
            models.UniqueConstraint(
                fields=("app", "external_user_id"), name="unique_entitlement_per_app_user"
            )
        ]

    def __str__(self):
        return f"{self.app.slug}:{self.external_user_id} {'payé' if self.is_paid else 'non payé'}"

    def payload(self) -> dict:
        """Le corps exact poussé à l'application (§7). Figé à l'émission."""
        return {
            "issued_at": timezone.now().isoformat(),
            "app": self.app.slug,
            "external_user_id": self.external_user_id,
            "is_paid": self.is_paid,
            "status": self.status,
            "plan": self.plan_code,
            "interval": self.interval,
            "quotas": self.quotas,
            "current_period_end": self.current_period_end.isoformat() if self.current_period_end else None,
            "grace_until": self.grace_until.isoformat() if self.grace_until else None,
            "stripe_customer_id": self.stripe_customer_id,
            "source": self.source,
        }


class EntitlementDelivery(models.Model):
    """Une tentative de livraison d'un droit à son application. Rejouable."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    STATUSES = [(PENDING, "En attente"), (DELIVERED, "Livré"), (FAILED, "Échec")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entitlement = models.ForeignKey(Entitlement, on_delete=models.CASCADE, related_name="deliveries")
    payload = models.JSONField()
    status = models.CharField(max_length=16, choices=STATUSES, default=PENDING)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name_plural = "entitlement deliveries"

    def __str__(self):
        return f"{self.id} {self.status}"
