from django.contrib.auth.backends import ModelBackend

from .constants import (
    DELEGABLE_COMPANY_PERMISSION_CODENAMES,
)
from .models import CompanyMembership
from .scope import (
    get_current_company,
    get_current_membership,
    get_current_permissions,
)


def _is_company_local_permission_code(permission_code):
    if permission_code.startswith(("pigs.", "transport.")):
        return True

    if not permission_code.startswith("companies."):
        return False

    _, codename = permission_code.split(".", 1)

    return (
        codename
        in DELEGABLE_COMPANY_PERMISSION_CODENAMES
    )


class CompanyRoleBackend(ModelBackend):
    """
    Permission-first local authorization backend.

    A role is only a base permission template. Effective authority comes
    from the active CompanyMembership permission set:

        role permissions + member grants - member denials

    CentralAuth is never contacted in the operational permission hot path.
    """

    def user_can_authenticate(self, user):
        if not super().user_can_authenticate(user):
            return False

        if getattr(
            user,
            "is_platform_admin",
            False,
        ):
            # Platform Admin must be able to enter Home Management before
            # the first company exists.
            return True

        # Every non-platform user must belong to at least one ACTIVE
        # company before being allowed into Home Management.
        return CompanyMembership.objects.active().filter(
            user=user,
        ).exists()

    def has_perm(
        self,
        user_obj,
        perm,
        obj=None,
    ):
        if (
            user_obj is None
            or not user_obj.is_authenticated
            or not user_obj.is_active
        ):
            return False

        if _is_company_local_permission_code(
            perm
        ):
            company = get_current_company()

            if company is None:
                return False

            if getattr(
                user_obj,
                "is_platform_admin",
                False,
            ):
                # Global platform authority is valid only after choosing a
                # concrete company for company-owned data.
                return True

            membership = get_current_membership()

            if membership is None:
                return False

            if (
                membership.user_id != user_obj.pk
                or membership.company_id != company.pk
            ):
                return False

            return (
                perm
                in get_current_permissions()
            )

        return super().has_perm(
            user_obj,
            perm,
            obj=obj,
        )

    def get_all_permissions(
        self,
        user_obj,
        obj=None,
    ):
        permissions = {
            permission
            for permission in super().get_all_permissions(
                user_obj,
                obj=obj,
            )
            if not _is_company_local_permission_code(
                permission
            )
        }

        if (
            user_obj is None
            or not user_obj.is_authenticated
            or not user_obj.is_active
        ):
            return permissions

        company = get_current_company()

        if company is None:
            return permissions

        if getattr(
            user_obj,
            "is_platform_admin",
            False,
        ):
            from django.contrib.auth.models import Permission

            for permission in (
                Permission.objects
                .filter(
                    content_type__app_label__in=[
                        "pigs",
                        "transport",
                        "companies",
                    ]
                )
                .select_related("content_type")
            ):
                code = (
                    f"{permission.content_type.app_label}."
                    f"{permission.codename}"
                )

                if _is_company_local_permission_code(
                    code
                ):
                    permissions.add(code)

            return permissions

        membership = get_current_membership()

        if (
            membership is None
            or membership.user_id != user_obj.pk
            or membership.company_id != company.pk
        ):
            return permissions

        permissions.update(
            get_current_permissions()
        )

        return permissions