from django.contrib.auth.backends import ModelBackend


class CustomUserBackend(ModelBackend):
    """
    Custom authentication backend kwa Pig Management System.

    Mfumo unatumia aina mbili za permissions:

    1. Django model permissions
       mfano:
           pigs.view_pig
           pigs.add_pig
           pigs.change_pig
           pigs.delete_pig

    2. Business permissions
       mfano:
           can_manage_pigs
           can_manage_sales
           can_manage_reports

    Direct user permissions kutoka Django
    zinaheshimiwa pamoja na permissions za roles.
    """

    # ============================================================
    # BUSINESS PERMISSIONS ZA ROLE
    # ============================================================

    def get_role_permissions(self, user_obj):

        permissions = set()

        # --------------------------------------------------------
        # SUPER ADMIN
        # --------------------------------------------------------

        if user_obj.is_superuser:

            permissions.update({
                "can_view_dashboard",
                "can_manage_pigs",
                "can_manage_purchases",
                "can_manage_slaughter",
                "can_manage_meat",
                "can_manage_sales",
                "can_manage_food",
                "can_manage_reports",
                "can_manage_users",
                "can_delete_data",
                "can_view_reports",
            })

            return permissions

        # --------------------------------------------------------
        # ADMIN
        # --------------------------------------------------------

        if user_obj.groups.filter(
            name="Admin"
        ).exists():

            permissions.update({
                "can_view_dashboard",
                "can_manage_pigs",
                "can_manage_purchases",
                "can_manage_slaughter",
                "can_manage_meat",
                "can_manage_sales",
                "can_manage_food",
                "can_manage_reports",
                "can_view_reports",
            })

            return permissions

        # --------------------------------------------------------
        # MANAGER
        # --------------------------------------------------------

        if user_obj.groups.filter(
            name="Manager"
        ).exists():

            permissions.update({
                "can_view_dashboard",
                "can_manage_pigs",
                "can_manage_purchases",
                "can_manage_slaughter",
                "can_manage_meat",
                "can_manage_sales",
                "can_manage_food",
                "can_manage_reports",
                "can_view_reports",
            })

            return permissions

        # --------------------------------------------------------
        # VIEWER
        # --------------------------------------------------------

        if user_obj.groups.filter(
            name="Viewer"
        ).exists():

            permissions.update({
                "can_view_dashboard",
                "can_view_reports",
            })

            return permissions

        return permissions

    # ============================================================
    # ALL PERMISSIONS
    # ============================================================

    def get_all_permissions(
        self,
        user_obj,
        obj=None
    ):

        if not user_obj.is_active:
            return set()

        # --------------------------------------------------------
        # SUPERUSER
        # --------------------------------------------------------

        if user_obj.is_superuser:
            return super().get_all_permissions(
                user_obj,
                obj
            )

        permissions = set()

        # --------------------------------------------------------
        # 1. DJANGO DIRECT USER PERMISSIONS
        # --------------------------------------------------------

        direct_permissions = (
            super().get_all_permissions(
                user_obj,
                obj
            )
        )

        permissions.update(
            direct_permissions
        )

        # --------------------------------------------------------
        # 2. ROLE BUSINESS PERMISSIONS
        # --------------------------------------------------------

        role_permissions = (
            self.get_role_permissions(
                user_obj
            )
        )

        permissions.update(
            role_permissions
        )

        return permissions

    # ============================================================
    # HAS PERMISSION
    # ============================================================

    def has_perm(
        self,
        user_obj,
        perm,
        obj=None
    ):

        if not user_obj.is_active:
            return False

        # --------------------------------------------------------
        # SUPERUSER
        # --------------------------------------------------------

        if user_obj.is_superuser:
            return True

        # --------------------------------------------------------
        # DIRECT DJANGO PERMISSION
        #
        # mfano:
        #
        # pigs.view_pig
        # pigs.change_pig
        #
        # --------------------------------------------------------

        django_permissions = (
            super().get_all_permissions(
                user_obj,
                obj
            )
        )

        if perm in django_permissions:
            return True

        # --------------------------------------------------------
        # BUSINESS PERMISSION
        #
        # mfano:
        #
        # can_manage_pigs
        # can_manage_sales
        #
        # --------------------------------------------------------

        if perm in self.get_role_permissions(
            user_obj
        ):
            return True

        return False

    # ============================================================
    # MODULE PERMISSIONS
    # ============================================================

    def has_module_perms(
        self,
        user_obj,
        app_label
    ):

        if not user_obj.is_active:
            return False

        if user_obj.is_superuser:
            return True

        # --------------------------------------------------------
        # DJANGO MODEL PERMISSIONS
        # --------------------------------------------------------

        django_permissions = (
            super().get_all_permissions(
                user_obj
            )
        )

        for permission in django_permissions:

            if permission.startswith(
                f"{app_label}."
            ):
                return True

        # --------------------------------------------------------
        # BUSINESS PERMISSIONS
        # --------------------------------------------------------

        business_permissions = (
            self.get_role_permissions(
                user_obj
            )
        )

        return bool(
            business_permissions
        )