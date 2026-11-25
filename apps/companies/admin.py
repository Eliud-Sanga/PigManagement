from django.contrib import admin

from .models import (
    Company,
    CompanyAuditEvent,
    CompanyMembership,
    CompanyRole,
)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "status",
        "default_language",
        "created_at",
    )
    list_filter = (
        "status",
        "default_language",
    )
    search_fields = (
        "name",
        "code",
        "email",
        "phone",
    )
    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
        "suspended_at",
        "archived_at",
    )

    # Company deletion cascades to governed records and business data.
    def has_delete_permission(self, request, obj=None):
        return False


class GovernedRecordsReadOnlyAdmin(admin.ModelAdmin):
    """Keep sensitive membership and role writes in reviewed workflows."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CompanyRole)
class CompanyRoleAdmin(GovernedRecordsReadOnlyAdmin):
    list_display = (
        "name",
        "code",
        "company",
        "is_reserved",
        "is_active",
    )
    list_filter = (
        "company",
        "is_reserved",
        "is_active",
    )
    search_fields = (
        "name",
        "code",
        "company__name",
    )
    filter_horizontal = (
        "permissions",
    )


@admin.register(CompanyMembership)
class CompanyMembershipAdmin(GovernedRecordsReadOnlyAdmin):
    list_display = (
        "user",
        "company",
        "role",
        "status",
        "requested_language",
        "approved_language",
    )
    list_filter = (
        "company",
        "status",
        "role",
        "requested_language",
        "approved_language",
    )
    search_fields = (
        "user__username",
        "user__email",
        "company__name",
        "company__code",
    )
    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
        "approved_at",
    )
    filter_horizontal = (
        "granted_permissions",
        "denied_permissions",
    )


@admin.register(CompanyAuditEvent)
class CompanyAuditEventAdmin(admin.ModelAdmin):
    list_display = (
        "action",
        "company",
        "actor",
        "result",
        "created_at",
    )
    list_filter = (
        "company",
        "action",
        "result",
    )
    search_fields = (
        "action",
        "description",
        "company__name",
        "actor__username",
    )
    readonly_fields = (
        "event_id",
        "company",
        "actor",
        "action",
        "result",
        "description",
        "metadata",
        "created_at",
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