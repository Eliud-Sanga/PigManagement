from django.core.management.base import BaseCommand, CommandError

from apps.pigs.stage4_demo import (
    DEFAULT_CREDENTIALS_FILE,
    cleanup_stage4_demo,
    verify_platform_admin,
)


class Command(BaseCommand):
    help = (
        "Remove Stage 4 demo business data/memberships/users and archive "
        "the exact demo company shells while preserving audit history."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--admin-username",
            default="",
        )
        parser.add_argument(
            "--credentials-file",
            default=str(DEFAULT_CREDENTIALS_FILE),
        )
        parser.add_argument(
            "--confirm-demo-cleanup",
            action="store_true",
            help=(
                "Required safety flag. Only exact Stage 4 demo markers "
                "are eligible for cleanup."
            ),
        )

    def handle(self, *args, **options):
        if not options["confirm_demo_cleanup"]:
            raise CommandError(
                "Cleanup requires --confirm-demo-cleanup."
            )

        admin = verify_platform_admin(
            options["admin_username"] or None
        )

        result = cleanup_stage4_demo(
            admin=admin,
            credentials_file=options[
                "credentials_file"
            ],
        )

        if result["cleaned"]:
            self.stdout.write(
                self.style.SUCCESS(
                    "Stage 4 demo business data cleaned. "
                    "Exact demo company shells were archived so audit "
                    "history remains intact."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "No Stage 4 demo companies were present."
                )
            )
