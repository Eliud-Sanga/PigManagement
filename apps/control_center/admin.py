from django.contrib import admin

from .models import (
    AccountLoginThrottle,
    BrowserLoginThrottle,
    SecurityAuditEvent,
    SecuritySettings,
)


@admin.register(SecuritySettings)
class SecuritySettingsAdmin(admin.ModelAdmin):
    """
    Django admin is deliberately read-only.

    Security policy changes must go through the PIGS Control Center,
    where every save requires the current Platform Administrator password.
    """

    list_display = (
        "singleton_key",
        "max_failed_login_attempts",
        "login_lock_minutes",
        "maintenance_enabled",
        "maintenance_ends_at",
        "updated_at",
    )

    readonly_fields = [
        field.name
        for field
        in SecuritySettings._meta.fields
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(
        self,
        request,
        obj=None,
    ):
        return False

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        return False


@admin.register(SecurityAuditEvent)
class SecurityAuditEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "severity",
        "result",
        "action",
        "actor_identifier",
        "ip_address",
    )

    list_filter = (
        "category",
        "severity",
        "result",
        "action",
    )

    search_fields = (
        "actor_identifier",
        "subject_identifier",
        "attempted_identifier",
        "reason_code",
        "description",
        "ip_address",
        "event_hash",
    )

    readonly_fields = [
        field.name
        for field
        in SecurityAuditEvent._meta.fields
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(
        self,
        request,
        obj=None,
    ):
        return False

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        return False


@admin.register(BrowserLoginThrottle)
class BrowserLoginThrottleAdmin(admin.ModelAdmin):
    list_display = (
        "browser_key_hash",
        "failed_attempts",
        "locked_until",
        "last_ip_address",
    )

    readonly_fields = (
        "browser_key_hash",
        "failed_attempts",
        "locked_until",
        "last_failed_at",
        "last_identifier_hash",
        "last_ip_address",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(
        self,
        request,
        obj=None,
    ):
        return False

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        return False


@admin.register(AccountLoginThrottle)
class AccountLoginThrottleAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "failed_attempts",
        "locked_until",
        "last_ip_address",
    )

    readonly_fields = (
        "user",
        "failed_attempts",
        "locked_until",
        "last_failed_at",
        "last_ip_address",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(
        self,
        request,
        obj=None,
    ):
        return False

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        return False
