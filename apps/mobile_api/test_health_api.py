from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse


class HealthProbeTests(TestCase):
    def test_public_liveness_has_no_sensitive_details(self):
        response = self.client.get(reverse("mobile_api:health_live"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_readiness_confirms_database(self):
        response = self.client.get(reverse("mobile_api:health_ready"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})
        self.assertIn("no-store", response.headers["Cache-Control"])


class FailedHealthProbeTests(TestCase):

    @patch("apps.mobile_api.views.connection")
    def test_readiness_failure_is_sanitized(self, database_connection):
        database_connection.cursor.side_effect = DatabaseError(
            "secret database host"
        )
        response = self.client.get(reverse("mobile_api:health_ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotContains(
            response,
            "secret database host",
            status_code=503,
        )
