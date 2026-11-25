from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase

from apps.users.security import (
    is_step_up_verified,
    mark_step_up_verified,
)


User = get_user_model()


class StepUpSecurityTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="admin",
            email="security-admin@example.test",
            password="SecurePassword123!",
            is_active=True,
            is_platform_admin=True,
        )

    def _request(self):
        request = self.factory.post(
            "/security/confirm/"
        )

        SessionMiddleware(
            lambda req: None
        ).process_request(
            request
        )

        request.session.save()
        request.user = self.user
        return request

    def test_correct_password_marks_step_up(self):
        request = self._request()

        mark_step_up_verified(
            request=request,
            password="SecurePassword123!",
        )

        self.assertTrue(
            is_step_up_verified(
                request=request,
            )
        )

    def test_wrong_password_denied(self):
        request = self._request()

        with self.assertRaises(PermissionDenied):
            mark_step_up_verified(
                request=request,
                password="WrongPassword",
            )

    def test_password_change_invalidates_step_up(self):
        request = self._request()

        mark_step_up_verified(
            request=request,
            password="SecurePassword123!",
        )

        self.user.set_password(
            "AnotherSecurePassword123!"
        )
        self.user.save(
            update_fields=["password"]
        )

        self.assertFalse(
            is_step_up_verified(
                request=request,
            )
        )
