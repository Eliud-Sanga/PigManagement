from django.test import TestCase, override_settings
from django.urls import reverse


class AndroidReleaseApiTests(TestCase):
    def test_updates_are_safely_disabled_until_release_is_configured(self):
        response = self.client.get(reverse("mobile_api:android_release"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "platform": "android",
                "channel": "apk",
                "enabled": False,
            },
        )
        self.assertIn("no-store", response.headers["Cache-Control"])

    @override_settings(
        BIZMANAGER_ANDROID_UPDATE_ENABLED=True,
        BIZMANAGER_ANDROID_LATEST_VERSION_NAME="1.1.0",
        BIZMANAGER_ANDROID_LATEST_VERSION_CODE=2,
        BIZMANAGER_ANDROID_MINIMUM_VERSION_CODE=1,
        BIZMANAGER_ANDROID_APK_URL="https://downloads.example.test/bizmanager.apk",
        BIZMANAGER_ANDROID_APK_SHA256="a" * 64,
        BIZMANAGER_ANDROID_RELEASE_NOTES="Secure cloud runtime.",
        BIZMANAGER_ANDROID_RELEASE_PUBLISHED_AT="2026-09-30T19:00:00Z",
    )
    def test_public_manifest_contains_only_reviewed_release_metadata(self):
        response = self.client.get(reverse("mobile_api:android_release"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["release"]["versionCode"], 2)
        self.assertEqual(payload["release"]["minimumVersionCode"], 1)
        self.assertEqual(payload["release"]["sha256"], "a" * 64)
        self.assertNotIn("secret", response.content.decode().lower())

    @override_settings(
        BIZMANAGER_ANDROID_UPDATE_ENABLED=True,
        BIZMANAGER_ANDROID_LATEST_VERSION_NAME="1.1.0",
        BIZMANAGER_ANDROID_LATEST_VERSION_CODE=2,
        BIZMANAGER_ANDROID_MINIMUM_VERSION_CODE=3,
        BIZMANAGER_ANDROID_APK_URL="http://downloads.example.test/bizmanager.apk",
        BIZMANAGER_ANDROID_APK_SHA256="not-a-digest",
    )
    def test_invalid_or_cleartext_release_configuration_is_sanitized(self):
        response = self.client.get(reverse("mobile_api:android_release"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "release_metadata_unavailable")
        self.assertNotContains(
            response,
            "downloads.example.test",
            status_code=503,
        )
