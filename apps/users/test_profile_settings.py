from django.test import TestCase
from django.urls import reverse

from apps.companies.services import create_company
from apps.users.models import User


class ProfileSettingsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="profile-admin",
            email="profile-admin@example.test",
            password="AdminPassword123!",
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )

        self.owner = User.objects.create_user(
            username="profile-owner",
            email="profile-owner@example.test",
            password="OwnerPassword123!",
            is_active=True,
        )

        self.company = create_company(
            actor=self.admin,
            name="Profile Settings Company",
            code="profile-settings-company",
            owner=self.owner,
            allowed_languages=["sw", "en"],
            default_language="sw",
            step_up_verified=True,
        )

    def test_owner_can_open_personal_settings(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("users:profile_settings")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Profile & Settings")

    def test_profile_save_requires_correct_password(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("users:profile_settings"),
            {
                "username": self.owner.username,
                "email": self.owner.email,
                "first_name": "Changed",
                "last_name": "",
                "preferred_language": "en",
                "theme_preference": "DARK",
                "current_password": "WrongPassword!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.owner.refresh_from_db()
        self.assertNotEqual(self.owner.first_name, "Changed")

    def test_profile_save_updates_preferences(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("users:profile_settings"),
            {
                "username": self.owner.username,
                "email": self.owner.email,
                "first_name": "Eliud",
                "last_name": "Owner",
                "preferred_language": "en",
                "theme_preference": "DARK",
                "current_password": "OwnerPassword123!",
            },
        )
        self.assertRedirects(
            response,
            reverse("users:profile_settings"),
        )
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, "Eliud")
        self.assertEqual(self.owner.preferred_language, "en")
        self.assertEqual(self.owner.theme_preference, "DARK")

    def test_owner_can_update_company_profile(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("users:owner_company_settings"),
            {
                "name": "Updated Company",
                "email": "business@example.test",
                "phone": "0712345678",
                "address": "Dodoma",
                "current_password": "OwnerPassword123!",
            },
        )
        self.assertRedirects(
            response,
            reverse("users:owner_company_settings"),
        )
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Updated Company")

    def test_company_profile_wrong_password_does_not_save(self):
        self.client.force_login(self.owner)
        original = self.company.name
        response = self.client.post(
            reverse("users:owner_company_settings"),
            {
                "name": "Should Not Save",
                "email": "",
                "phone": "",
                "address": "",
                "current_password": "WrongPassword!",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, original)

    def test_platform_admin_can_open_personal_settings(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("users:profile_settings")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Platform Administrator")

    def test_platform_admin_cannot_use_owner_company_settings(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("users:owner_company_settings")
        )
        self.assertEqual(response.status_code, 403)
