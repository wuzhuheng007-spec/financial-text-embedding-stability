from django.contrib import admin

from .models import Invitation, ProviderState, PublicFactCard, PublicMarketSnapshot, UserPreferences, WatchlistItem, WorkerHeartbeat


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "created_at", "expires_at", "consumed_at")
    readonly_fields = ("email", "token_digest", "created_at", "expires_at", "consumed_at", "created_by")
    search_fields = ("email",)

    def has_add_permission(self, request):
        return False  # Use create_invite; the raw code is shown only once to the operator.


@admin.register(PublicFactCard)
class PublicFactCardAdmin(admin.ModelAdmin):
    list_display = ("issuer", "fiscal_period", "metric", "source_class", "display_approved")
    list_filter = ("source_class", "display_approved")
    search_fields = ("issuer", "metric")


@admin.register(PublicMarketSnapshot)
class PublicMarketSnapshotAdmin(admin.ModelAdmin):
    list_display = ("symbol", "provider", "provider_time", "source_class", "display_approved")
    list_filter = ("source_class", "display_approved")


admin.site.register((WatchlistItem, UserPreferences, ProviderState, WorkerHeartbeat))
