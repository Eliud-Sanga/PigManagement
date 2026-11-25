from io import StringIO

from django.core.management import call_command
from django.test import TestCase


class SecurityIntegrityCommandTests(TestCase):
    def test_read_only_integrity_command_accepts_current_schema(self):
        output = StringIO()
        call_command("verify_business_integrity", stdout=output)
        self.assertIn("Database integrity verified", output.getvalue())
