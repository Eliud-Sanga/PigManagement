from uuid import uuid4

from django.conf import settings
from django.test import SimpleTestCase

from apps.users.integrations.centralauth import CentralAuthDisabled


class CentralAuthBoundaryTests(SimpleTestCase):
    def test_centralauth_is_disabled_during_standalone_rebuild(self):
        self.assertFalse(
            settings.CENTRALAUTH_ENABLED
        )

    def test_disabled_adapter_fails_closed(self):
        adapter = CentralAuthDisabled()

        with self.assertRaises(RuntimeError):
            adapter.refresh_identity(
                subject=uuid4()
            )
