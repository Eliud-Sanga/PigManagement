import re
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.control_center.models import SecurityAuditEvent
from apps.users.models import PasswordRecoveryChallenge, User


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="security@example.test",
)
class PasswordRecoveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="recover-me",
            email="recover@example.test",
            password="OldStrongPass123!",
            first_name="Eliud",
            last_name="Sanga",
            phone="+255712345678",
            is_active=True,
        )
        self.identity = {
            "username": "recover-me",
            "first_name": "Eliud",
            "last_name": "Sanga",
            "email": "recover@example.test",
            "phone": "0712345678",
        }

    def start(self, data=None):
        return self.client.post(
            reverse("users:password_recovery"),
            data or self.identity,
        )

    def code(self):
        self.assertEqual(len(mail.outbox), 1)
        match = re.search(r"\b(\d{6})\b", mail.outbox[0].body)
        self.assertIsNotNone(match)
        return match.group(1)

    def verify(self, code):
        return self.client.post(
            reverse("users:password_recovery_verify"),
            {"code": code},
        )

    def test_start_page_loads(self):
        response = self.client.get(reverse("users:password_recovery"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thibitisha akaunti")

    def test_wrong_identity_does_not_send_email(self):
        data = dict(self.identity)
        data["last_name"] = "Wrong"
        response = self.start(data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(PasswordRecoveryChallenge.objects.count(), 0)

    def test_correct_identity_sends_hashed_six_digit_code(self):
        response = self.start()
        self.assertRedirects(response, reverse("users:password_recovery_verify"))
        code = self.code()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())
        challenge = PasswordRecoveryChallenge.objects.get()
        self.assertNotEqual(challenge.code_hash, code)
        self.assertNotIn(code, challenge.code_hash)

    def test_wrong_code_does_not_verify(self):
        self.start()
        actual = self.code()
        wrong = "000000" if actual != "000000" else "111111"
        response = self.verify(wrong)
        self.assertEqual(response.status_code, 200)
        challenge = PasswordRecoveryChallenge.objects.get()
        self.assertIsNone(challenge.verified_at)
        self.assertEqual(challenge.failed_attempts, 1)

    def test_correct_code_opens_password_step(self):
        self.start()
        code = self.code()
        response = self.verify(code)
        self.assertRedirects(response, reverse("users:password_recovery_reset"))
        challenge = PasswordRecoveryChallenge.objects.get()
        self.assertIsNotNone(challenge.verified_at)

    def test_weak_numeric_password_is_rejected(self):
        self.start()
        code = self.code()
        self.verify(code)
        response = self.client.post(
            reverse("users:password_recovery_reset"),
            {"new_password1": "1234567890", "new_password2": "1234567890"},
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("OldStrongPass123!"))

    def test_successful_reset_changes_password_and_uses_challenge(self):
        self.start()
        code = self.code()
        self.verify(code)
        response = self.client.post(
            reverse("users:password_recovery_reset"),
            {
                "new_password1": "BrandNewSecurePass987!",
                "new_password2": "BrandNewSecurePass987!",
            },
        )
        self.assertRedirects(response, reverse("users:login"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("BrandNewSecurePass987!"))
        self.assertFalse(self.user.check_password("OldStrongPass123!"))
        self.assertIsNotNone(self.user.password_changed_at)
        self.assertIsNotNone(PasswordRecoveryChallenge.objects.get().used_at)
        self.assertTrue(
            SecurityAuditEvent.objects.filter(
                action="PASSWORD_RECOVERY_COMPLETE",
                subject_user=self.user,
            ).exists()
        )

    def test_used_challenge_cannot_reset_twice(self):
        self.start()
        code = self.code()
        self.verify(code)
        self.client.post(
            reverse("users:password_recovery_reset"),
            {
                "new_password1": "BrandNewSecurePass987!",
                "new_password2": "BrandNewSecurePass987!",
            },
        )
        response = self.client.get(reverse("users:password_recovery_reset"))
        self.assertRedirects(response, reverse("users:password_recovery"))

    def test_missing_stored_phone_requires_admin(self):
        self.user.phone = ""
        self.user.save(update_fields=["phone"])
        response = self.start()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
