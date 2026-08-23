from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.users.models import AuditLog

from apps.pigs.models import (
    Purchase,
    Pig,
    SlaughterBatch,
    MeatStock,
    DailySale,
    FoodItem,
    FoodSaleRecord,
    PigSaleRecord,
    MeatPartSale,
    BatchProfitReport,
    DailySaleReport,
)


class Command(BaseCommand):

    help = "Reset all business data while preserving database schema and system users."

    USERS = {
        "eliud": {
            "email": "eliudsanga@gmail.com",
            "role": "Super Admin",
        },
        "jane@admin": {
            "email": "eliudsanga@gmail.com",
            "role": "Admin",
        },
        "jane": {
            "email": "eliudsanga@gmail.com",
            "role": "Manager",
        },
        "eliusa": {
            "email": "eliudsanga@gmail.com",
            "role": "Viewer",
        },
    }

    def handle(self, *args, **options):

        User = get_user_model()

        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "WARNING: This will permanently delete business data."
            )
        )
        self.stdout.write(
            "Database migrations/schema will NOT be deleted."
        )
        self.stdout.write(
            "The four system users will be preserved."
        )
        self.stdout.write("")

        confirmation = input(
            "Type RESET to continue: "
        ).strip()

        if confirmation != "RESET":
            raise CommandError(
                "Reset cancelled."
            )

        try:

            with transaction.atomic():

                deleted_counts = {}

                models_to_reset = [
                    BatchProfitReport,
                    DailySaleReport,
                    FoodSaleRecord,
                    PigSaleRecord,
                    MeatPartSale,
                    DailySale,
                    FoodItem,
                    MeatStock,
                    SlaughterBatch,
                    Pig,
                    Purchase,
                ]

                for model in models_to_reset:

                    count, _ = model.objects.all().delete()

                    deleted_counts[
                        model.__name__
                    ] = count

                AuditLog.objects.all().delete()

                self.stdout.write("")
                self.stdout.write(
                    self.style.SUCCESS(
                        "Business data reset successfully."
                    )
                )

                self.stdout.write("")

                self.stdout.write(
                    "Deleted records:"
                )

                for model_name, count in deleted_counts.items():

                    self.stdout.write(
                        f"  {model_name}: {count}"
                    )

                self.stdout.write(
                    "  AuditLog: reset"
                )

                self.stdout.write("")
                self.stdout.write(
                    "Checking system users..."
                )

                required_usernames = set(
                    self.USERS.keys()
                )

                existing_users = set(
                    User.objects.filter(
                        username__in=required_usernames
                    ).values_list(
                        "username",
                        flat=True
                    )
                )

                missing_users = (
                    required_usernames
                    - existing_users
                )

                if missing_users:

                    raise CommandError(
                        "Required users are missing: "
                        + ", ".join(
                            sorted(missing_users)
                        )
                    )

                eliud = User.objects.get(
                    username="eliud"
                )

                if not eliud.is_superuser:

                    raise CommandError(
                        "User 'eliud' is not configured "
                        "as Super Admin."
                    )

                normal_users = User.objects.filter(
                    username__in=[
                        "jane@admin",
                        "jane",
                        "eliusa",
                    ]
                )

                inactive_users = normal_users.filter(
                    is_active=False
                )

                if inactive_users.exists():

                    raise CommandError(
                        "One or more normal system users "
                        "are inactive."
                    )

                self.stdout.write("")
                self.stdout.write(
                    self.style.SUCCESS(
                        "System users confirmed:"
                    )
                )

                for username, details in self.USERS.items():

                    user = User.objects.get(
                        username=username
                    )

                    if username == "eliud":

                        status = (
                            "Super Admin"
                            if user.is_superuser
                            else "INVALID"
                        )

                    else:

                        status = details["role"]

                    self.stdout.write(
                        f"  {username} -> {status}"
                    )

                self.stdout.write("")

                self.stdout.write(
                    self.style.SUCCESS(
                        "RESET COMPLETED SUCCESSFULLY."
                    )
                )

                self.stdout.write(
                    "Database schema and migrations are untouched."
                )

        except CommandError:
            raise

        except Exception as exc:

            raise CommandError(
                f"Reset failed. No changes were committed. "
                f"Reason: {exc}"
            )
