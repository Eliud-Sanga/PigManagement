from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.companies.models import (
    Company,
    CompanyAuditEvent,
    CompanyMembership,
)
from apps.companies.services import (
    create_company,
    request_company_user,
)
from apps.users.models import AuditLog


User = get_user_model()


class AdminControlCenterTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin-control",
            email="admin-control@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )

        self.client.force_login(
            self.admin,
            backend=(
                "apps.companies.backends."
                "CompanyRoleBackend"
            ),
        )

    def _create_company(self, *, code="alpha-company", owner=None):
        owner = (
            owner
            or User.objects.create_user(
                username=f"owner-{code}",
                email=f"owner-{code}@example.test",
                password="OwnerPassword123!",
                is_active=True,
            )
        )

        return create_company(
            actor=self.admin,
            name=f"Company {code}",
            code=code,
            owner=owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )

    def test_company_create_ui_creates_owner_and_forces_first_change(self):
        response = self.client.post(
            reverse("users:company_create"),
            {
                "name": "Alpha Pork",
                "code": "alpha-pork",
                "email": "",
                "phone": "",
                "address": "Dodoma",
                "timezone": "Africa/Dar_es_Salaam",
                "allowed_languages": ["sw", "en"],
                "default_language": "sw",
                "owner_email": "owner-alpha@example.test",
                "owner_username": "owner-alpha",
                "owner_first_name": "Owner",
                "owner_last_name": "Alpha",
                "temporary_password1": "OwnerTemporary123!",
                "temporary_password2": "OwnerTemporary123!",
                "admin_password": "AdminPassword123!",
            },
        )

        self.assertEqual(response.status_code, 302)

        company = Company.objects.get(code="alpha-pork")
        owner = User.objects.get(
            email="owner-alpha@example.test"
        )

        self.assertTrue(owner.must_change_password)
        self.assertTrue(
            owner.check_password("OwnerTemporary123!")
        )
        self.assertTrue(
            CompanyMembership.objects.filter(
                company=company,
                user=owner,
                role__code="OWNER",
                status=CompanyMembership.Status.ACTIVE,
            ).exists()
        )

    def test_same_owner_identity_can_own_multiple_companies(self):
        owner = User.objects.create_user(
            username="multi-owner",
            email="multi-owner@example.test",
            password="OwnerPassword123!",
            is_active=True,
        )

        first = self._create_company(
            code="one",
            owner=owner,
        )

        response = self.client.post(
            reverse("users:company_create"),
            {
                "name": "Company Two",
                "code": "two",
                "email": "",
                "phone": "",
                "address": "",
                "timezone": "Africa/Dar_es_Salaam",
                "allowed_languages": ["sw", "en"],
                "default_language": "en",
                "owner_email": owner.email,
                "owner_username": "",
                "owner_first_name": "",
                "owner_last_name": "",
                "temporary_password1": "",
                "temporary_password2": "",
                "admin_password": "AdminPassword123!",
            },
        )

        self.assertEqual(response.status_code, 302)

        second = Company.objects.get(code="two")
        self.assertNotEqual(first.pk, second.pk)

        self.assertEqual(
            CompanyMembership.objects.filter(
                user=owner,
                role__code="OWNER",
                status=CompanyMembership.Status.ACTIVE,
            ).count(),
            2,
        )

    def test_company_edit_requires_correct_admin_password(self):
        company = self._create_company()

        response = self.client.post(
            reverse(
                "users:company_edit",
                args=[company.public_id],
            ),
            {
                "name": "Changed Name",
                "email": "",
                "phone": "",
                "address": "",
                "timezone": "Africa/Dar_es_Salaam",
                "admin_password": "WrongPassword!",
            },
        )

        self.assertEqual(response.status_code, 200)

        company.refresh_from_db()
        self.assertNotEqual(company.name, "Changed Name")

    def test_pending_user_approval_uses_secure_activation(self):
        from django.core import mail
        from apps.companies.models import CompanyUserActivation

        company = self._create_company(
            code="approval-company"
        )

        role = company.roles.get(code="STAFF")

        membership = request_company_user(
            actor=self.admin,
            company=company,
            role=role,
            username="pending-staff",
            email="pending-staff@example.test",
            requested_language="sw",
        )

        self.assertFalse(
            membership.user.has_usable_password()
        )
        self.assertFalse(
            membership.user.is_active
        )

        self.client.force_login(self.admin)

        with self.settings(
            EMAIL_BACKEND=(
                "django.core.mail.backends.locmem.EmailBackend"
            ),
            DEFAULT_FROM_EMAIL="security@example.test",
        ):
            response = self.client.post(
                reverse(
                    "users:approval_review",
                    args=[membership.public_id],
                ),
                {
                    "decision": "approve",
                    "approved_language": "sw",
                    "admin_password": "AdminPassword123!",
                },
            )

        self.assertEqual(response.status_code, 302)

        membership.refresh_from_db()
        membership.user.refresh_from_db()

        self.assertEqual(
            membership.status,
            CompanyMembership.Status.ACTIVE,
        )
        self.assertFalse(
            membership.user.is_active
        )
        self.assertFalse(
            membership.user.has_usable_password()
        )
        self.assertFalse(
            membership.user.must_change_password
        )

        activation = CompanyUserActivation.objects.get(
            membership=membership
        )

        self.assertEqual(len(activation.token_hash), 64)
        self.assertIsNotNone(activation.sent_at)
        self.assertIsNone(activation.used_at)
        self.assertEqual(len(mail.outbox), 1)


    def test_last_active_owner_cannot_be_suspended(self):
        company = self._create_company(
            code="owner-protection"
        )

        membership = CompanyMembership.objects.get(
            company=company,
            role__code="OWNER",
        )

        response = self.client.post(
            reverse(
                "users:membership_status",
                args=[
                    membership.public_id,
                    "suspend",
                ],
            ),
            {
                "admin_password":
                    "AdminPassword123!",
            },
        )

        self.assertEqual(response.status_code, 302)

        membership.refresh_from_db()
        self.assertEqual(
            membership.status,
            CompanyMembership.Status.ACTIVE,
        )

    def test_member_permission_override_does_not_mutate_role(self):
        company = self._create_company(
            code="override-company"
        )

        user = User.objects.create_user(
            username="viewer-user",
            email="viewer-user@example.test",
            password="ViewerPassword123!",
            is_active=True,
        )

        role = company.roles.get(code="VIEWER")

        membership = CompanyMembership.objects.create(
            company=company,
            user=user,
            role=role,
            status=CompanyMembership.Status.ACTIVE,
            requested_language="sw",
            approved_language="sw",
            created_by=self.admin,
            approved_by=self.admin,
        )

        permission = (
            Permission.objects
            .filter(
                content_type__app_label="pigs",
                codename__startswith="add_",
            )
            .select_related("content_type")
            .first()
        )

        self.assertIsNotNone(permission)

        code = (
            f"{permission.content_type.app_label}."
            f"{permission.codename}"
        )

        self.assertFalse(
            role.permissions.filter(
                pk=permission.pk
            ).exists()
        )

        response = self.client.post(
            reverse(
                "users:member_permissions",
                args=[membership.public_id],
            ),
            {
                "granted_permissions": [code],
                "denied_permissions": [],
                "admin_password":
                    "AdminPassword123!",
            },
        )

        self.assertEqual(response.status_code, 302)

        membership.refresh_from_db()

        self.assertTrue(
            membership.granted_permissions
            .filter(pk=permission.pk)
            .exists()
        )
        self.assertFalse(
            role.permissions.filter(
                pk=permission.pk
            ).exists()
        )


    def test_temporary_password_forces_first_change_before_business(self):
        company = self._create_company(
            code="forced-change-company"
        )

        owner_membership = CompanyMembership.objects.get(
            company=company,
            role__code="OWNER",
        )

        owner = owner_membership.user
        owner.set_password("TemporaryOwner123!")
        owner.must_change_password = True
        owner.save(
            update_fields=[
                "password",
                "must_change_password",
            ]
        )

        self.client.logout()

        response = self.client.post(
            reverse("users:login"),
            {
                "identifier": owner.email,
                "password": "TemporaryOwner123!",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse(
                "users:user_change_password",
                args=[owner.pk],
            ),
        )

        blocked = self.client.get(
            reverse("pigs:dashboard")
        )

        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(
            blocked.url,
            reverse(
                "users:user_change_password",
                args=[owner.pk],
            ),
        )


    def test_legacy_audit_log_is_append_only(self):
        event = AuditLog.objects.create(
            user=self.admin,
            action="SECURITY",
            result="SUCCESS",
            description="immutable test",
        )

        event.description = "tamper"

        with self.assertRaises(ValidationError):
            event.save()

        with self.assertRaises(ValidationError):
            AuditLog.objects.filter(
                pk=event.pk
            ).update(
                description="tamper"
            )

        with self.assertRaises(ValidationError):
            AuditLog.objects.filter(
                pk=event.pk
            ).delete()

    def test_company_audit_is_append_only(self):
        company = self._create_company(
            code="audit-company"
        )

        event = (
            CompanyAuditEvent.objects
            .filter(company=company)
            .first()
        )

        self.assertIsNotNone(event)

        event.description = "tamper"

        with self.assertRaises(ValidationError):
            event.save()

        with self.assertRaises(ValidationError):
            CompanyAuditEvent.objects.filter(
                pk=event.pk
            ).update(
                description="tamper"
            )

        with self.assertRaises(ValidationError):
            CompanyAuditEvent.objects.filter(
                pk=event.pk
            ).delete()

    def test_reusing_existing_owner_does_not_allow_silent_global_password_reset(self):
        owner = User.objects.create_user(
            username="safe-multi-owner",
            email="safe-multi-owner@example.test",
            password="ExistingOwnerPassword123!",
            is_active=True,
        )

        response = self.client.post(
            reverse("users:company_create"),
            {
                "name": "Password Safety Company",
                "code": "password-safety-company",
                "email": "",
                "phone": "",
                "address": "",
                "timezone": "Africa/Dar_es_Salaam",
                "allowed_languages": ["sw", "en"],
                "default_language": "sw",
                "owner_email": owner.email,
                "owner_username": "",
                "owner_first_name": "",
                "owner_last_name": "",
                "temporary_password1": "UnexpectedResetPassword123!",
                "temporary_password2": "UnexpectedResetPassword123!",
                "admin_password": "AdminPassword123!",
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        owner.refresh_from_db()

        self.assertTrue(
            owner.check_password(
                "ExistingOwnerPassword123!"
            )
        )

        self.assertFalse(
            owner.check_password(
                "UnexpectedResetPassword123!"
            )
        )

        self.assertFalse(
            Company.objects.filter(
                code="password-safety-company"
            ).exists()
        )


    def test_approving_existing_identity_does_not_allow_silent_global_password_reset(self):
        company = self._create_company(
            code="existing-user-approval"
        )

        existing = User.objects.create_user(
            username="existing-staff",
            email="existing-staff@example.test",
            password="ExistingStaffPassword123!",
            is_active=True,
        )

        membership = request_company_user(
            actor=self.admin,
            company=company,
            role=company.roles.get(code="STAFF"),
            username=existing.username,
            email=existing.email,
            requested_language="sw",
        )

        response = self.client.post(
            reverse(
                "users:approval_review",
                args=[membership.public_id],
            ),
            {
                "decision": "approve",
                "approved_language": "sw",
                "temporary_password1": "UnexpectedStaffReset123!",
                "temporary_password2": "UnexpectedStaffReset123!",
                "admin_password": "AdminPassword123!",
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        membership.refresh_from_db()
        existing.refresh_from_db()

        self.assertEqual(
            membership.status,
            CompanyMembership.Status.PENDING_APPROVAL,
        )

        self.assertTrue(
            existing.check_password(
                "ExistingStaffPassword123!"
            )
        )

        self.assertFalse(
            existing.check_password(
                "UnexpectedStaffReset123!"
            )
        )

