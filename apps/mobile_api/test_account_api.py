from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from apps.companies.constants import ROLE_MANAGER, ROLE_STAFF
from apps.companies.models import CompanyAuditEvent, CompanyMembership
from apps.companies.services import (
    approve_company_membership, create_company, request_company_user,
)
from apps.users.models import User
from . import views


PASSWORD = "OwnerPassword123!"
BASE = "/api/mobile/v1/account/"


class MobileDevelopmentCorsTests(SimpleTestCase):
    def csrf_response(self, origin):
        request = RequestFactory().get("/api/mobile/v1/auth/csrf/", HTTP_ORIGIN=origin)
        return views.csrf(request)

    @override_settings(DEBUG=True, CSRF_TRUSTED_ORIGINS=["http://192.168.1.20:5173"])
    def test_explicit_trusted_lan_origin_can_receive_csrf_credentials(self):
        origin = "http://192.168.1.20:5173"
        response = self.csrf_response(origin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], origin)
        self.assertEqual(response["Access-Control-Allow-Credentials"], "true")
        self.assertIn("origin", response["Vary"].lower())
        self.assertIn("no-store", response["Cache-Control"])

    @override_settings(DEBUG=True, CSRF_TRUSTED_ORIGINS=["http://192.168.1.20:5173"])
    def test_unlisted_lan_and_lookalike_origins_cannot_receive_credentials(self):
        for origin in ("http://192.168.1.21:5173", "http://192.168.1.20.example.invalid:5173"):
            with self.subTest(origin=origin):
                response = self.csrf_response(origin)
                self.assertNotIn("Access-Control-Allow-Origin", response)
                self.assertNotIn("Access-Control-Allow-Credentials", response)

    @override_settings(DEBUG=True, CSRF_TRUSTED_ORIGINS=[
        "http://192.168.1.20:5174", "https://192.168.1.20:5173",
    ])
    def test_trusted_origins_outside_the_dev_scheme_and_port_are_not_relaxed(self):
        for origin in ("http://192.168.1.20:5174", "https://192.168.1.20:5173"):
            with self.subTest(origin=origin):
                response = self.csrf_response(origin)
                self.assertNotIn("Access-Control-Allow-Origin", response)
                self.assertNotIn("Access-Control-Allow-Credentials", response)

    @override_settings(DEBUG=False, CSRF_TRUSTED_ORIGINS=["http://192.168.1.20:5173"])
    def test_production_does_not_enable_development_cors(self):
        for origin in ("http://localhost:5173", "http://127.0.0.1:5173", "http://192.168.1.20:5173"):
            with self.subTest(origin=origin):
                response = self.csrf_response(origin)
                self.assertNotIn("Access-Control-Allow-Origin", response)
                self.assertNotIn("Access-Control-Allow-Credentials", response)


class MobileAccountApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="reviewer", email="reviewer@example.test", password=PASSWORD,
            is_active=True, is_platform_admin=True,
        )
        self.owner = User.objects.create_user(
            username="owner", email="owner@example.test", password=PASSWORD, is_active=True,
        )
        self.company = create_company(
            actor=self.admin, name="Owner Company", code="owner-company", owner=self.owner,
            allowed_languages=["sw", "en"], default_language="sw", step_up_verified=True,
        )
        self.other_owner = User.objects.create_user(
            username="other", email="other@example.test", password=PASSWORD, is_active=True,
        )
        self.other_company = create_company(
            actor=self.admin, name="Other Company", code="other-company", owner=self.other_owner,
            allowed_languages=["sw"], default_language="sw", step_up_verified=True,
        )
        self.member = CompanyMembership.objects.get(company=self.company, user=self.owner)
        self.client.force_login(self.owner)

    def enable_team(self):
        self.company.owner_user_management_enabled = True
        self.company.save(update_fields=["owner_user_management_enabled"])

    def team_payload(self):
        return {
            "username": "new-staff", "email": "new-staff@example.test",
            "first_name": "New", "last_name": "Staff", "phone": "+255700000002",
            "role": str(self.company.roles.get(code=ROLE_MANAGER).pk),
            "requested_language": "sw", "current_password": PASSWORD,
        }

    def active_colleague(self):
        self.enable_team()
        member = request_company_user(
            actor=self.owner, company=self.company,
            role=self.company.roles.get(code=ROLE_MANAGER),
            username=self.other_owner.username, email=self.other_owner.email,
            requested_language="sw",
        )
        approve_company_membership(
            actor=self.admin, membership=member, approved_language="sw",
            step_up_verified=True,
        )
        return CompanyMembership.objects.get(pk=member.pk)

    def test_owner_can_see_member_roles_and_delegable_permissions(self):
        colleague = self.active_colleague()
        account = self.client.get(BASE).json()
        self.assertTrue(account["capabilities"]["manageMemberPermissions"])
        self.assertIn("pigs.view_purchase", {p["code"] for p in account["permissions"]})
        member = self.client.get(BASE + f"team/{colleague.public_id}/").json()["member"]
        self.assertTrue(member["canEditRole"])
        self.assertTrue(member["canEditPermissions"])
        self.assertIn("pigs.view_purchase", member["effectivePermissions"])
        self.assertEqual(member["roleId"], str(colleague.role_id))
        self.assertFalse(self.client.get(BASE + f"team/{self.member.public_id}/").json()
                         ["member"]["canEditRole"])

    def test_owner_can_change_company_member_role_but_not_own_or_foreign_owner(self):
        colleague = self.active_colleague()
        url = BASE + f"team/{colleague.public_id}/role/"
        staff = self.company.roles.get(code=ROLE_STAFF)
        response = self.client.post(url, {"role": staff.pk, "current_password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.content)
        colleague.refresh_from_db()
        self.assertEqual(colleague.role_id, staff.pk)
        self.assertTrue(CompanyAuditEvent.objects.filter(
            company=self.company, action="OWNER_MEMBER_ROLE_CHANGED").exists())
        self.assertEqual(self.client.post(
            BASE + f"team/{self.member.public_id}/role/",
            {"role": staff.pk, "current_password": PASSWORD}).status_code, 403)
        foreign = CompanyMembership.objects.get(company=self.other_company, user=self.other_owner)
        self.assertEqual(self.client.post(
            BASE + f"team/{foreign.public_id}/role/",
            {"role": staff.pk, "current_password": PASSWORD}).status_code, 404)

    def test_role_change_rejects_wrong_password_foreign_role_owner_role_and_escalation(self):
        colleague = self.active_colleague()
        url = BASE + f"team/{colleague.public_id}/role/"
        for role, password, expected in (
            (self.company.roles.get(code=ROLE_STAFF), "wrong", 400),
            (self.other_company.roles.get(code=ROLE_MANAGER), PASSWORD, 400),
            (self.company.roles.get(code="OWNER"), PASSWORD, 400),
        ):
            result = self.client.post(url, {"role": role.pk, "current_password": password})
            self.assertEqual(result.status_code, expected, result.content)
        self.member.denied_permissions.add(Permission.objects.get(
            content_type__app_label="pigs", codename="view_purchase"))
        self.assertEqual(self.client.post(url, {
            "role": self.company.roles.get(code=ROLE_STAFF).pk,
            "current_password": PASSWORD,
        }).status_code, 403)
        colleague.refresh_from_db()
        self.assertEqual(colleague.role.code, ROLE_MANAGER)

    def test_owner_can_grant_and_deny_permissions_with_password_and_company_scope(self):
        colleague = self.active_colleague()
        url = BASE + f"team/{colleague.public_id}/permissions/"
        form = {
            "granted_permissions": "pigs.delete_purchase",
            "denied_permissions": "pigs.view_purchase",
            "current_password": PASSWORD,
        }
        self.assertEqual(self.client.post(url, {**form, "current_password": "wrong"}).status_code, 400)
        result = self.client.post(url, form)
        self.assertEqual(result.status_code, 200, result.content)
        permissions = result.json()["member"]["effectivePermissions"]
        self.assertIn("pigs.delete_purchase", permissions)
        self.assertNotIn("pigs.view_purchase", permissions)
        self.assertEqual(self.client.post(
            BASE + f"team/{self.member.public_id}/permissions/", form).status_code, 403)
        foreign = CompanyMembership.objects.get(company=self.other_company, user=self.other_owner)
        self.assertEqual(self.client.post(
            BASE + f"team/{foreign.public_id}/permissions/", form).status_code, 404)

    def test_permission_update_rejects_escalation_overlap_and_policy_denial(self):
        colleague = self.active_colleague()
        url = BASE + f"team/{colleague.public_id}/permissions/"
        self.member.denied_permissions.add(Permission.objects.get(
            content_type__app_label="pigs", codename="delete_purchase"))
        self.assertEqual(self.client.post(url, {
            "granted_permissions": "pigs.delete_purchase",
            "denied_permissions": "", "current_password": PASSWORD,
        }).status_code, 403)
        self.assertEqual(self.client.post(url, {
            "granted_permissions": "pigs.view_purchase",
            "denied_permissions": "pigs.view_purchase", "current_password": PASSWORD,
        }).status_code, 400)
        self.company.owner_user_management_enabled = False
        self.company.save(update_fields=["owner_user_management_enabled"])
        self.assertEqual(self.client.post(url, {
            "granted_permissions": "", "denied_permissions": "",
            "current_password": PASSWORD,
        }).status_code, 403)
        colleague.refresh_from_db()
        self.assertEqual(colleague.granted_permissions.count(), 0)

    def test_overview_is_scoped_and_contains_no_secrets(self):
        result = self.client.get(BASE)
        self.assertEqual(result.status_code, 200)
        payload = result.json()
        self.assertEqual(payload["company"]["id"], str(self.company.public_id))
        self.assertEqual(payload["profile"]["email"], self.owner.email)
        self.assertTrue(payload["capabilities"]["editCompany"])
        self.assertFalse(payload["capabilities"]["manageUsers"])
        self.assertNotIn("password", payload["profile"])
        self.assertNotIn("is_platform_admin", payload["profile"])

    def test_anonymous_and_platform_admin_are_denied(self):
        self.client.logout()
        self.assertEqual(self.client.get(BASE).status_code, 401)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(BASE).status_code, 403)
        self.assertEqual(self.client.post(BASE + "profile/update/", {}).status_code, 403)

    def test_profile_requires_password_and_does_not_change_on_failure(self):
        response = self.client.post(BASE + "profile/update/", {"first_name": "Changed", "current_password": "wrong"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("current_password", response.json()["fields"])
        self.owner.refresh_from_db()
        self.assertNotEqual(self.owner.first_name, "Changed")

    def test_profile_update_preserves_omitted_fields_and_cannot_elevate(self):
        response = self.client.post(BASE + "profile/update/", {
            "first_name": "Eliud", "current_password": PASSWORD,
            "is_platform_admin": "true", "is_superuser": "true", "id": str(self.other_owner.pk),
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.owner.refresh_from_db()
        self.other_owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, "Eliud")
        self.assertEqual(self.owner.email, "owner@example.test")
        self.assertFalse(self.owner.is_platform_admin)
        self.assertFalse(self.owner.is_superuser)
        self.assertNotEqual(self.other_owner.first_name, "Eliud")
        self.assertTrue(CompanyAuditEvent.objects.filter(company=self.company, action="USER_SELF_PROFILE_UPDATED").exists())

    def test_duplicate_email_and_invalid_language_rejected(self):
        response = self.client.post(BASE + "profile/update/", {"email": self.other_owner.email, "current_password": PASSWORD})
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json()["fields"])
        response = self.client.post(BASE + "profile/update/", {"preferred_language": "invalid", "current_password": PASSWORD})
        self.assertEqual(response.status_code, 400)

    def test_company_update_targets_only_active_company(self):
        response = self.client.post(BASE + "company/update/", {
            "name": "Updated Company", "current_password": PASSWORD,
            "company_id": str(self.other_company.pk), "owner_user_management_enabled": "true",
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.company.refresh_from_db()
        self.other_company.refresh_from_db()
        self.assertEqual(self.company.name, "Updated Company")
        self.assertEqual(self.other_company.name, "Other Company")
        self.assertFalse(self.company.owner_user_management_enabled)

    def test_company_update_requires_password(self):
        response = self.client.post(BASE + "company/update/", {"name": "Denied", "current_password": "wrong"})
        self.assertEqual(response.status_code, 400)
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Owner Company")

    def test_non_owner_cannot_edit_company_or_manage_team(self):
        self.enable_team()
        self.member.role = self.company.roles.get(code=ROLE_MANAGER)
        self.member.save(update_fields=["role"])
        self.assertEqual(self.client.post(BASE + "company/update/", {"name": "Denied", "current_password": PASSWORD}).status_code, 403)
        self.assertEqual(self.client.get(BASE + "team/").status_code, 403)

    def test_team_policy_and_explicit_permission_denial_are_enforced(self):
        self.assertEqual(self.client.get(BASE + "team/").status_code, 403)
        self.enable_team()
        self.member.denied_permissions.add(Permission.objects.get(content_type__app_label="companies", codename="manage_company_users"))
        self.assertEqual(self.client.get(BASE + "team/").status_code, 403)
        self.assertEqual(self.client.post(BASE + "team/request/", self.team_payload()).status_code, 403)

    def test_team_request_creates_pending_membership_not_access(self):
        self.enable_team()
        response = self.client.post(BASE + "team/request/", self.team_payload())
        self.assertEqual(response.status_code, 201, response.content)
        member = CompanyMembership.objects.get(company=self.company, user__username="new-staff")
        self.assertEqual(member.status, CompanyMembership.Status.PENDING_APPROVAL)
        self.assertFalse(member.user.is_active)
        self.assertFalse(member.user.has_usable_password())
        self.assertEqual(member.user.phone, "+255700000002")
        self.assertEqual(self.client.post(BASE + "team/request/", self.team_payload()).status_code, 400)
        self.assertEqual(CompanyMembership.objects.filter(company=self.company, user__username="new-staff").count(), 1)

    def test_team_request_does_not_overwrite_existing_global_identity(self):
        self.enable_team()
        self.other_owner.first_name = "Existing"
        self.other_owner.phone = "original-phone"
        self.other_owner.save()
        payload = {**self.team_payload(), "username": "other", "email": self.other_owner.email}
        response = self.client.post(BASE + "team/request/", payload)
        self.assertEqual(response.status_code, 201, response.content)
        self.other_owner.refresh_from_db()
        self.assertEqual(self.other_owner.first_name, "Existing")
        self.assertEqual(self.other_owner.phone, "original-phone")

    def test_team_request_rejects_wrong_password_foreign_role_and_owner_role(self):
        self.enable_team()
        for updates in (
            {"current_password": "wrong"},
            {"role": str(self.other_company.roles.get(code=ROLE_MANAGER).pk)},
            {"role": str(self.company.roles.get(code="OWNER").pk)},
        ):
            response = self.client.post(BASE + "team/request/", {**self.team_payload(), **updates})
            self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(User.objects.filter(username="new-staff").exists())

    def test_team_list_is_paginated_and_scoped(self):
        self.enable_team()
        payload = self.client.get(BASE + "team/").json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["username"], "owner")
        self.assertFalse(payload["items"][0]["canArchive"])
        self.assertEqual(self.client.get(BASE + "team/?q=other").json()["count"], 0)

    def test_platform_admin_identity_cannot_be_requested_as_staff(self):
        self.enable_team()
        response = self.client.post(BASE + "team/request/", {
            **self.team_payload(), "username": self.admin.username, "email": self.admin.email,
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CompanyMembership.objects.filter(user=self.admin).exists())

    def test_role_permissions_cannot_exceed_requesting_owner(self):
        self.enable_team()
        self.member.denied_permissions.add(Permission.objects.get(
            content_type__app_label="pigs", codename="view_purchase"))
        response = self.client.post(BASE + "team/request/", self.team_payload())
        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(User.objects.filter(username="new-staff").exists())

    def test_team_archive_preserves_identity_and_cannot_target_other_company(self):
        self.enable_team()
        self.client.post(BASE + "team/request/", self.team_payload())
        member = CompanyMembership.objects.get(company=self.company, user__username="new-staff")
        path = BASE + f"team/{member.public_id}/archive/"
        self.assertEqual(self.client.post(path, {"current_password": "wrong"}).status_code, 400)
        self.assertEqual(self.client.post(path, {"current_password": PASSWORD}).status_code, 200)
        member.refresh_from_db()
        self.assertEqual(member.status, CompanyMembership.Status.ARCHIVED)
        self.assertTrue(User.objects.filter(pk=member.user_id).exists())
        foreign = CompanyMembership.objects.get(company=self.other_company)
        response = self.client.post(BASE + f"team/{foreign.public_id}/archive/", {"current_password": PASSWORD})
        self.assertEqual(response.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, CompanyMembership.Status.ACTIVE)

    def test_owner_cannot_archive_their_membership(self):
        self.enable_team()
        response = self.client.post(BASE + f"team/{self.member.public_id}/archive/", {"current_password": PASSWORD})
        self.assertEqual(response.status_code, 403)

    def test_password_change_preserves_this_session_and_revokes_other_session(self):
        other_client = Client()
        other_client.force_login(self.owner)
        new_password = "DifferentSecret!42628"
        response = self.client.post(BASE + "password/change/", {
            "current_password": PASSWORD, "new_password1": new_password, "new_password2": new_password,
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(new_password))
        self.assertFalse(self.owner.check_password(PASSWORD))
        self.assertEqual(self.client.get(BASE).status_code, 200)
        self.assertEqual(other_client.get(BASE).status_code, 401)

    def test_forced_password_change_allows_only_required_mobile_auth_routes(self):
        # Use a real company member: CompanyRoleBackend deliberately refuses
        # to authenticate non-platform accounts without an active membership.
        # A standalone temporary user would be logged out by that backend
        # before the session endpoint runs, making this test assert the wrong
        # behavior (401 instead of the forced-password payload).
        user = self.owner
        user.must_change_password = True
        user.save(update_fields=["must_change_password"])
        client = self.client

        session_response = client.get("/api/mobile/v1/auth/session/")
        self.assertEqual(session_response.status_code, 200)
        self.assertTrue(session_response.json()["user"]["requiresPasswordChange"])

        csrf_response = client.get("/api/mobile/v1/auth/csrf/")
        self.assertEqual(csrf_response.status_code, 200)
        self.assertIn("csrfToken", csrf_response.json())

        blocked = client.get("/api/mobile/v1/workspace/")
        self.assertEqual(blocked.status_code, 302)

        new_password = "Temporary-Changed-Password-532!"
        changed = client.post("/api/mobile/v1/account/password/change/", {
            "current_password": PASSWORD,
            "new_password1": new_password,
            "new_password2": new_password,
        })
        self.assertEqual(changed.status_code, 200, changed.content)
        user.refresh_from_db()
        self.assertFalse(user.must_change_password)
        self.assertTrue(user.check_password(new_password))

    @override_settings(AUTH_PASSWORD_VALIDATORS=[{"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"}])
    def test_password_change_rejects_weak_mismatched_and_wrong_current_password(self):
        for current, first, second in (("wrong", "A-new-secret!", "A-new-secret!"),
                                      (PASSWORD, "short", "short"), (PASSWORD, "A-new-secret!", "Mismatch!")):
            response = self.client.post(BASE + "password/change/", {
                "current_password": current, "new_password1": first, "new_password2": second,
            })
            self.assertEqual(response.status_code, 400, response.content)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(PASSWORD))

    def test_mutation_endpoints_are_post_only_and_csrf_protected(self):
        self.enable_team()
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        for path in ("profile/update/", "company/update/", "password/change/", "team/request/", f"team/{self.member.public_id}/archive/"):
            self.assertEqual(csrf_client.post(BASE + path, {}).status_code, 403)
            self.assertEqual(self.client.get(BASE + path).status_code, 405)
