from django.contrib.auth.backends import ModelBackend

from .models import SSOIdentity
from .services.central_auth import (
    CentralAuthPermissionError,
    get_central_permissions,
)


class CustomUserBackend(ModelBackend):
    """
    PIGS authentication and authorization backend.

    Authentication:
        Delegated to Django ModelBackend.

    System authorization:
        Resolved exclusively by CentralAuth.

    PIGS permissions use the format:
        pigs.<permission_code>
    """

    def get_central_permissions(self, user_obj):
        """
        Resolve CentralAuth permissions for the current PIGS user.

        Permissions are cached on the user object so multiple
        has_perm() calls during one request do not trigger
        multiple HTTP requests to CentralAuth.
        """

        if user_obj is None or not user_obj.is_active:
            return set()

        cached = getattr(
            user_obj,
            "_centralauth_permissions_cache",
            None,
        )

        if cached is not None:
            return cached

        identity = (
            SSOIdentity.objects
            .only("central_subject")
            .filter(
                user=user_obj,
            )
            .first()
        )

        if identity is None:
            permissions = set()

        else:
            try:
                permissions = get_central_permissions(
                    identity.central_subject,
                )

            except CentralAuthPermissionError:
                # Security principle:
                # authorization failure must fail closed.
                permissions = set()

        user_obj._centralauth_permissions_cache = permissions

        return permissions

    def get_all_permissions(
        self,
        user_obj,
        obj=None,
    ):
        """
        Return effective PIGS system permissions from CentralAuth.
        """

        if user_obj is None or not user_obj.is_active:
            return set()

        if user_obj.is_superuser:
            return super().get_all_permissions(
                user_obj,
                obj,
            )

        return self.get_central_permissions(
            user_obj,
        )

    def has_perm(
        self,
        user_obj,
        perm,
        obj=None,
    ):
        """
        Resolve authorization.

        PIGS system permissions are decided exclusively by
        CentralAuth.

        Django Groups and Django direct permissions do not
        grant pigs.* authorization.
        """

        if user_obj is None or not user_obj.is_active:
            return False

        if user_obj.is_superuser:
            return True

        perm = (perm or "").strip().lower()

        if not perm.startswith("pigs."):
            return False

        return perm in self.get_central_permissions(
            user_obj,
        )

    def has_module_perms(
        self,
        user_obj,
        app_label,
    ):
        """
        Determine whether the user has any permissions for PIGS.
        """

        if user_obj is None or not user_obj.is_active:
            return False

        if user_obj.is_superuser:
            return True

        app_label = (app_label or "").strip().lower()

        if app_label != "pigs":
            return False

        return bool(
            self.get_central_permissions(
                user_obj,
            )
        )