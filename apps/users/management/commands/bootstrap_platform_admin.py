from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


User = get_user_model()


class Command(BaseCommand):
    help = (
        "Create the first Platform Administrator securely. "
        "No default password exists in source code."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            required=True,
        )
        parser.add_argument(
            "--email",
            required=True,
        )

    def handle(
        self,
        *args,
        **options,
    ):
        if User.objects.filter(
            is_platform_admin=True,
            is_active=True,
        ).exists():
            raise CommandError(
                "An active Platform Administrator already exists."
            )

        username = options["username"].strip()
        email = options["email"].strip().lower()

        password = getpass(
            "Platform Admin password: "
        )
        confirmation = getpass(
            "Confirm password: "
        )

        if password != confirmation:
            raise CommandError(
                "Passwords do not match."
            )

        user = User(
            username=username,
            email=email,
            is_active=True,
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
        )

        try:
            validate_password(
                password,
                user=user,
            )
        except ValidationError as exc:
            raise CommandError(
                " ".join(exc.messages)
            ) from exc

        user.set_password(password)
        user.full_clean()
        user.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Platform Administrator created: {username}"
            )
        )
