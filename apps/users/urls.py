from django.urls import path

from . import views as legacy_views
from . import profile_views
from . import recovery_views
from . import activation_views
from apps.control_center import views as control_views
from apps.control_center import company_views
from apps.companies import user_management_views
from apps.companies import company_hub_views
from apps.companies import onboarding_views


app_name = "users"


urlpatterns = [
    path("login/", control_views.secure_login, name="login"),
    path("logout/", control_views.secure_logout, name="logout"),
    path(
        "language/switch/",
        profile_views.language_switch,
        name="language_switch",
    ),
    path("password-recovery/", recovery_views.recovery_start, name="password_recovery"),
    path("password-recovery/verify/", recovery_views.recovery_verify, name="password_recovery_verify"),
    path("password-recovery/resend/", recovery_views.recovery_resend, name="password_recovery_resend"),
    path("password-recovery/reset/", recovery_views.recovery_reset, name="password_recovery_reset"),
    path(
        "activate/complete/",
        activation_views.company_user_activation_complete,
        name="company_user_activation_complete",
    ),
    path(
        "activate/<uuid:activation_id>/<str:token>/",
        activation_views.company_user_activation_handoff,
        name="company_user_activation_handoff",
    ),
    path(
        "profile/settings/",
        profile_views.profile_settings,
        name="profile_settings",
    ),
    path(
        "company/settings/",
        profile_views.owner_company_settings,
        name="owner_company_settings",
    ),
    path(
        "company/",
        company_hub_views.company_hub,
        name="company_hub",
    ),
    path("maintenance/", control_views.maintenance_status, name="maintenance"),

    path("control-center/", control_views.dashboard, name="control_center"),
    path(
        "control-center/policies/",
        control_views.policy_console,
        name="policy_console",
    ),
    path("control-center/security/", control_views.security_settings, name="security_settings"),
    path("control-center/audit/", control_views.audit_list, name="audit_logs"),
    path("control-center/audit/<uuid:event_id>/", control_views.audit_detail, name="audit_detail"),

    path("control-center/companies/", company_views.company_list, name="company_list"),
    path("control-center/companies/create/", company_views.company_create_view, name="company_create"),
    path("control-center/companies/<uuid:company_id>/", company_views.company_detail, name="company_detail"),
    path("control-center/companies/<uuid:company_id>/edit/", company_views.company_edit, name="company_edit"),
    path("control-center/companies/<uuid:company_id>/languages/", company_views.company_languages, name="company_languages"),
    path("control-center/companies/<uuid:company_id>/status/<str:action>/", company_views.company_status_action, name="company_status"),
    path("control-center/companies/<uuid:company_id>/members/request/", company_views.company_member_request, name="company_member_request"),
    path("control-center/companies/<uuid:company_id>/roles/", company_views.company_roles, name="company_roles"),
    path("control-center/companies/<uuid:company_id>/roles/create/", company_views.role_create, name="role_create"),
    path("control-center/companies/<uuid:company_id>/audit/", company_views.company_audit, name="company_audit"),
    path(
        "control-center/companies/<uuid:company_id>/owner-user-management/",
        company_views.company_owner_user_management_policy,
        name="company_owner_user_management_policy",
    ),

    path("control-center/approvals/", company_views.approval_list, name="approvals"),
    path("control-center/approvals/<uuid:membership_id>/", company_views.approval_review, name="approval_review"),
    path("control-center/memberships/<uuid:membership_id>/", company_views.company_member_detail, name="company_member_detail"),
    path(
        "control-center/memberships/<uuid:membership_id>/activation/resend/",
        company_views.company_member_activation_resend,
        name="company_member_activation_resend",
    ),
    path("control-center/memberships/<uuid:membership_id>/edit/", company_views.company_member_edit, name="company_member_edit"),
    path("control-center/memberships/<uuid:membership_id>/status/<str:action>/", company_views.membership_status_action, name="membership_status"),
    path("control-center/memberships/<uuid:membership_id>/permissions/", company_views.membership_permissions, name="member_permissions"),
    path("control-center/roles/<int:role_id>/permissions/", company_views.role_permissions, name="role_permissions"),

    path("company-users/", user_management_views.company_user_list, name="company_users"),
    path("company-users/add/", user_management_views.company_user_request, name="company_user_request"),
    path(
        "company-users/<uuid:membership_id>/remove/",
        user_management_views.company_user_remove,
        name="company_user_remove",
    ),

    path("users/", control_views.user_directory, name="user_list"),
    path("users/<int:user_id>/", control_views.user_detail, name="user_detail"),
    path("users/<int:user_id>/edit/", control_views.secure_user_edit, name="user_edit"),
    path("users/<int:user_id>/change-password/", control_views.secure_password_change, name="user_change_password"),

    path("users/create/", control_views.retired_legacy_admin, name="user_create"),
    path("users/<int:user_id>/delete/", control_views.retired_legacy_admin, name="user_delete"),
    path("users/<int:user_id>/toggle-active/", control_views.retired_legacy_admin, name="user_toggle_active"),
    path("users/<int:user_id>/grant-permission/", control_views.retired_legacy_admin, name="user_grant_permission"),
    path("users/<int:user_id>/revoke-permission/<int:perm_id>/", control_views.retired_legacy_admin, name="user_revoke_permission"),
    path("users/<int:user_id>/add-to-group/", control_views.retired_legacy_admin, name="user_add_to_group"),
    path("users/<int:user_id>/remove-from-group/<int:group_id>/", control_views.retired_legacy_admin, name="user_remove_from_group"),
    path("users/<int:user_id>/permissions/", control_views.retired_legacy_admin, name="user_permissions"),
    path("users/<int:user_id>/verify-permissions/", control_views.retired_legacy_admin, name="verify_permission_access"),

    path("data-management/", legacy_views.data_management, name="data_management"),
    path("data-management/<str:model_key>/", legacy_views.data_management_model, name="data_management_model"),
    path("data-management/<str:model_key>/<int:record_id>/edit/", legacy_views.data_management_edit, name="data_management_edit"),
    path("data-management/<str:model_key>/<int:record_id>/delete/", legacy_views.data_management_delete, name="data_management_delete"),

    path("sso/start/", legacy_views.sso_start, name="sso_start"),
    path("sso/callback/", legacy_views.sso_callback, name="sso_callback"),

    # PIGS-COMPANY-ONBOARDING-STAGE1-URLS
    path("control-center/invitations/", onboarding_views.admin_invitation_list, name="owner_invitation_list"),
    path("control-center/invitations/create/", onboarding_views.admin_invitation_create, name="owner_invitation_create"),
    path("control-center/invitations/<uuid:invitation_id>/", onboarding_views.admin_invitation_detail, name="owner_invitation_detail"),
    path("control-center/invitations/<uuid:invitation_id>/resend/", onboarding_views.admin_invitation_resend, name="owner_invitation_resend"),
    path("control-center/invitations/<uuid:invitation_id>/revoke/", onboarding_views.admin_invitation_revoke, name="owner_invitation_revoke"),
    path("company-onboarding/<uuid:invitation_id>/<str:token>/", onboarding_views.company_onboarding_register, name="company_onboarding_register"),

    # PIGS-COMPANY-ONBOARDING-STAGE2-URLS
    path(
        "control-center/invitations/<uuid:invitation_id>/approve/",
        onboarding_views.admin_invitation_approve,
        name="owner_invitation_approve",
    ),
    path(
        "control-center/invitations/<uuid:invitation_id>/reject/",
        onboarding_views.admin_invitation_reject,
        name="owner_invitation_reject",
    ),
    path(
        "control-center/invitations/<uuid:invitation_id>/continuation-resend/",
        onboarding_views.admin_invitation_continuation_resend,
        name="owner_invitation_continuation_resend",
    ),
    path(
        "company-onboarding/<uuid:invitation_id>/continue/<str:token>/",
        onboarding_views.company_onboarding_continue,
        name="company_onboarding_continue",
    ),

    # PIGS-COMPANY-ONBOARDING-STAGE3-URLS
    path(
        "company-onboarding/<uuid:invitation_id>/complete/<str:token>/",
        onboarding_views.company_onboarding_complete,
        name="company_onboarding_complete",
    ),
    path(
        "company-onboarding/<uuid:invitation_id>/action/login-now/",
        onboarding_views.company_onboarding_login_now,
        name="company_onboarding_login_now",
    ),
]
