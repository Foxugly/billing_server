from django.contrib import admin

from .models import App, AppCustomer, Entitlement, EntitlementDelivery, Plan


class PlanInline(admin.TabularInline):
    model = Plan
    extra = 0
    fields = ("code", "name", "price_monthly", "price_yearly", "quotas", "sort_order", "public", "active")


@admin.register(App)
class AppAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "base_url", "active")
    list_filter = ("active",)
    search_fields = ("slug", "name")
    inlines = [PlanInline]
    # Le secret HMAC ne se modifie pas à la main : sa rotation est un geste dédié,
    # qui doit accepter l'ancien et le nouveau pendant la fenêtre de bascule (lot L3).
    readonly_fields = ("shared_secret", "created_at")


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("app", "code", "name", "sort_order", "public", "active")
    list_filter = ("app", "public", "active")
    search_fields = ("code", "name")


@admin.register(AppCustomer)
class AppCustomerAdmin(admin.ModelAdmin):
    list_display = ("email", "app", "external_user_id", "customer", "created_at")
    list_filter = ("app",)
    search_fields = ("email", "external_user_id")
    readonly_fields = ("created_at",)


@admin.register(Entitlement)
class EntitlementAdmin(admin.ModelAdmin):
    list_display = ("app", "external_user_id", "is_paid", "status", "plan_code", "source", "computed_at")
    list_filter = ("app", "is_paid", "source", "status")
    search_fields = ("external_user_id",)
    # Ces champs sont dérivés d'un recalcul : les éditer à la main créerait un état
    # que le prochain webhook écraserait sans prévenir. Pour offrir un accès, passer
    # `source` à "manual" — recompute_entitlement() respecte alors la décision.
    readonly_fields = ("status", "current_period_end", "grace_until", "computed_at")


@admin.register(EntitlementDelivery)
class EntitlementDeliveryAdmin(admin.ModelAdmin):
    list_display = ("id", "entitlement", "status", "attempts", "next_retry_at", "delivered_at")
    list_filter = ("status",)
    search_fields = ("entitlement__external_user_id",)
    # Le payload est figé à l'émission : le modifier après coup rendrait l'historique
    # de livraison mensonger.
    readonly_fields = ("id", "entitlement", "payload", "attempts", "last_error", "delivered_at", "created_at")
