"""Sérialiseurs de la console d'exploitation.

Distincts de l'API service-à-service : la console lit des données d'exploitation
(qui paie quoi, quelles livraisons ont échoué), là où les apps ne voient que leur
propre périmètre.
"""
from rest_framework import serializers

from .models import App, AppCustomer, Entitlement, EntitlementDelivery, Plan


class AppSerializer(serializers.ModelSerializer):
    entitlement_url = serializers.CharField(read_only=True)
    plans_count = serializers.IntegerField(source="plans.count", read_only=True)

    class Meta:
        model = App
        fields = (
            "id", "slug", "name", "base_url", "entitlement_path", "entitlement_url",
            "active", "plans_count", "secret_rotated_at", "created_at",
        )
        # Le secret n'est JAMAIS exposé, même à un opérateur : la console n'en a
        # pas besoin (elle teste la connectivité côté serveur) et le faire
        # transiter dans un bundle SPA le mettrait dans un cache navigateur.
        read_only_fields = ("secret_rotated_at", "created_at")


class PlanSerializer(serializers.ModelSerializer):
    app_slug = serializers.CharField(source="app.slug", read_only=True)
    price_monthly_amount = serializers.SerializerMethodField()
    price_yearly_amount = serializers.SerializerMethodField()

    class Meta:
        model = Plan
        fields = (
            "id", "app", "app_slug", "code", "name", "description",
            "price_monthly", "price_yearly", "price_monthly_amount", "price_yearly_amount",
            "quotas", "per_unit_quota_key", "trial_days", "sort_order", "public", "active",
        )

    def get_price_monthly_amount(self, plan):
        return plan.price_monthly.unit_amount if plan.price_monthly else None

    def get_price_yearly_amount(self, plan):
        return plan.price_yearly.unit_amount if plan.price_yearly else None


class EntitlementSerializer(serializers.ModelSerializer):
    app_slug = serializers.CharField(source="app.slug", read_only=True)

    class Meta:
        model = Entitlement
        fields = (
            "id", "app", "app_slug", "external_user_id", "is_paid", "status",
            "plan_code", "interval", "quotas", "current_period_end", "grace_until",
            "stripe_customer_id", "source", "computed_at",
        )
        read_only_fields = fields


class AppCustomerSerializer(serializers.ModelSerializer):
    app_slug = serializers.SerializerMethodField()
    is_direct = serializers.BooleanField(read_only=True)

    class Meta:
        model = AppCustomer
        fields = ("id", "app", "app_slug", "is_direct", "external_user_id", "email", "customer", "created_at")
        read_only_fields = ("created_at",)

    def get_app_slug(self, customer):
        return customer.app.slug if customer.app else None


class EntitlementDeliverySerializer(serializers.ModelSerializer):
    app_slug = serializers.CharField(source="entitlement.app.slug", read_only=True)
    external_user_id = serializers.CharField(source="entitlement.external_user_id", read_only=True)

    class Meta:
        model = EntitlementDelivery
        fields = (
            "id", "app_slug", "external_user_id", "status", "attempts",
            "last_error", "next_retry_at", "delivered_at", "created_at", "payload",
        )
        read_only_fields = fields
