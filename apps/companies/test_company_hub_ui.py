from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.companies.constants import ROLE_MANAGER
from apps.companies.models import CompanyMembership
from apps.companies.services import (
    create_company,
    ensure_default_roles,
)

User = get_user_model()


class CompanyHubUITests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="hub-admin",
            email="hub-admin@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )

        self.owner = User.objects.create_user(
            username="hub-owner",
            email="hub-owner@example.test",
            password="OwnerPassword123!",
            is_active=True,
        )

        self.manager = User.objects.create_user(
            username="hub-manager",
            email="hub-manager@example.test",
            password="ManagerPassword123!",
            is_active=True,
        )

        # ADMIN_POLICY_CONSOLE_V7_EXPLICIT_TEST_LANGUAGE
        # These legacy UI tests assert English wording. Make that
        # language expectation explicit without changing production
        # company defaults or runtime language policy.
        self.owner.preferred_language = "en"
        self.owner.save()

        self.manager.preferred_language = "en"
        self.manager.save()

        self.company = create_company(
            actor=self.admin,
            name="Hub Company A",
            code="hub-company-a",
            owner=self.owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )

        roles = ensure_default_roles(self.company)

        self.manager_membership = CompanyMembership.objects.create(
            company=self.company,
            user=self.manager,
            role=roles[ROLE_MANAGER],
            status=CompanyMembership.Status.ACTIVE,
            requested_language="sw",
            approved_language="sw",
            created_by=self.admin,
            approved_by=self.admin,
        )

    def test_owner_can_open_company_hub(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("users:company_hub"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hub Company A")
        self.assertContains(response, "Manage your company")
        self.assertContains(response, "Company accounts")

    def test_owner_management_disabled_is_visible_but_not_linked(self):
        self.assertFalse(self.company.owner_user_management_enabled)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("users:company_hub"))
        self.assertContains(
            response,
            "Platform Admin enablement required",
        )
        self.assertNotContains(
            response,
            reverse("users:company_users"),
        )

    def test_owner_management_enabled_exposes_team_actions(self):
        self.company.owner_user_management_enabled = True
        self.company.save(
            update_fields=[
                "owner_user_management_enabled",
                "updated_at",
            ]
        )

        self.client.force_login(self.owner)
        response = self.client.get(reverse("users:company_hub"))

        self.assertContains(
            response,
            reverse("users:company_users"),
        )
        self.assertContains(
            response,
            reverse("users:company_user_request"),
        )

        users_response = self.client.get(
            reverse("users:company_users")
        )
        self.assertEqual(users_response.status_code, 200)

    def test_manager_gets_read_oriented_company_hub(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("users:company_hub"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your workspace")
        self.assertNotContains(
            response,
            "Edit company profile",
        )

    def test_platform_admin_cannot_use_business_company_hub(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("users:company_hub"))
        self.assertEqual(response.status_code, 403)

    def test_company_hub_is_tenant_specific(self):
        other_owner = User.objects.create_user(
            username="hub-owner-b",
            email="hub-owner-b@example.test",
            password="OwnerPassword123!",
            is_active=True,
        )

        create_company(
            actor=self.admin,
            name="Hub Company B",
            code="hub-company-b",
            owner=other_owner,
            allowed_languages=["sw", "en"],
            default_language="en",
            step_up_verified=True,
        )

        self.client.force_login(self.owner)
        response = self.client.get(reverse("users:company_hub"))

        self.assertContains(response, "Hub Company A")
        self.assertNotContains(response, "Hub Company B")

    def test_company_is_final_primary_navigation_item(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("pigs:dashboard"))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode(
            "utf-8",
            errors="ignore",
        )

        import re

        start = content.find('<div class="nav-main">')
        self.assertGreaterEqual(start, 0)

        depth = 0
        end = -1

        for match in re.finditer(
            r"<div\b[^>]*>|</div\s*>",
            content[start:],
            flags=re.IGNORECASE,
        ):
            token = match.group(0).lower()

            if token.startswith("<div"):
                depth += 1
                continue

            depth -= 1

            if depth == 0:
                end = start + match.start()
                break

        self.assertGreater(end, start)

        nav = content[start:end]
        company_href = reverse("users:company_hub")

        anchors = re.findall(
            r"<a\b[^>]*>",
            nav,
            flags=re.IGNORECASE,
        )

        primary_hrefs = []

        for anchor in anchors:
            class_match = re.search(
                r'class=["\']([^"\']*)["\']',
                anchor,
                flags=re.IGNORECASE,
            )

            if (
                class_match is None
                or "nav-link"
                not in class_match.group(1).split()
            ):
                continue

            href_match = re.search(
                r'href=["\']([^"\']+)["\']',
                anchor,
                flags=re.IGNORECASE,
            )

            if href_match is not None:
                primary_hrefs.append(
                    href_match.group(1)
                )

        self.assertTrue(primary_hrefs)
        self.assertEqual(
            primary_hrefs[-1],
            company_href,
        )
        self.assertEqual(
            primary_hrefs.count(company_href),
            1,
        )
