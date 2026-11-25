from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class BackupCommandTests(SimpleTestCase):
    def test_output_must_be_an_exact_file(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesMessage(CommandError, "exact snapshot file"):
                call_command("bizmanager_backup", output=directory)
