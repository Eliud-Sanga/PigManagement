from django.core.management.base import BaseCommand

from apps.pigs.stage4_demo import (
    DEFAULT_CREDENTIALS_FILE,
    seed_stage4_demo,
    verify_platform_admin,
)


class Command(BaseCommand):
    help = (
        "Create exactly two controlled Stage 4 demo companies and "
        "representative pig-business data. Existing real data is untouched."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--admin-username",
            default="",
            help=(
                "Platform Admin username. Optional only when exactly "
                "one active Platform Admin exists."
            ),
        )
        parser.add_argument(
            "--credentials-file",
            default=str(DEFAULT_CREDENTIALS_FILE),
            help="Secure output file for generated demo credentials.",
        )

    def handle(self, *args, **options):
        admin = verify_platform_admin(
            options["admin_username"] or None
        )

        result = seed_stage4_demo(
            admin=admin,
            credentials_file=options[
                "credentials_file"
            ],
        )

        if result["created"]:
            self.stdout.write(
                self.style.SUCCESS(
                    "Stage 4 demo data created successfully."
                )
            )
            self.stdout.write(
                f"Credentials: {result['credentials_file']}"
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "Stage 4 demo data already exists and passed "
                    "the idempotency verification. No changes applied."
                )
            )
