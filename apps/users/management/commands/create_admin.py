from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the initial Pig Management superuser."

    def handle(self, *args, **options):
        User = get_user_model()

        username = "eliud"
        email = "eliudsanga19@gmail.com"
        password = "1234567890"

        user = User.objects.filter(username=username).first()

        if user:
            if not user.is_superuser:
                user.is_staff = True
                user.is_superuser = True
                user.save(
                    update_fields=[
                        "is_staff",
                        "is_superuser",
                    ]
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"User '{username}' promoted to superuser."
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"Superuser '{username}' already exists."
                    )
                )

            return

        User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Superuser '{username}' created successfully."
            )
        )