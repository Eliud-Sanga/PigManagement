from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create and configure the initial Pig Management users."

    def handle(self, *args, **options):
        User = get_user_model()

        password = "1234567890"
        email = "eliudsanga@gmail.com"

        users = {
            "eliud": "Super Admin",
            "jane@admin": "Admin",
            "jane": "Manager",
            "eliusa": "Viewer",
        }

        all_models = [
            "purchase",
            "pig",
            "slaughterbatch",
            "meatstock",
            "dailysale",
            "pigsalerecord",
            "meatpartsale",
            "fooditem",
            "foodsalerecord",
            "batchprofitreport",
            "dailysalereport",
            "expense",
        ]

        sales_models = [
            "dailysale",
            "pigsalerecord",
            "meatpartsale",
            "foodsalerecord",
        ]

        purchase_models = [
            "purchase",
            "pig",
        ]

        def get_permissions(codenames):
            return Permission.objects.filter(
                content_type__app_label="pigs",
                codename__in=codenames,
            )

        view_permissions = [
            f"view_{model}"
            for model in all_models
        ]

        add_permissions = [
            f"add_{model}"
            for model in all_models
        ]

        change_permissions = [
            f"change_{model}"
            for model in all_models
        ]

        admin_permissions = (
            get_permissions(view_permissions)
            | get_permissions(add_permissions)
            | get_permissions(change_permissions)
        )

        manager_permissions = (
            get_permissions(view_permissions)
            | get_permissions(add_permissions)
            | get_permissions(
                [
                    f"change_{model}"
                    for model in (
                        sales_models + purchase_models
                    )
                ]
            )
        )

        viewer_permissions = get_permissions(
            view_permissions
        )

        groups = {}

        for role in ["Admin", "Manager", "Viewer"]:
            group, _ = Group.objects.get_or_create(
                name=role
            )
            groups[role] = group

        groups["Admin"].permissions.set(
            admin_permissions
        )

        groups["Manager"].permissions.set(
            manager_permissions
        )

        groups["Viewer"].permissions.set(
            viewer_permissions
        )

        for username, role in users.items():

            user = User.objects.filter(
                username=username
            ).first()

            if user is None:
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"User '{username}' created."
                    )
                )

            else:
                user.email = email
                user.is_active = True

                self.stdout.write(
                    self.style.WARNING(
                        f"User '{username}' already exists."
                    )
                )

            if username == "eliud":

                user.is_staff = True
                user.is_superuser = True
                user.set_password(password)

                user.groups.clear()
                user.user_permissions.clear()

                user.save()

                self.stdout.write(
                    self.style.SUCCESS(
                        "Super Admin 'eliud' configured."
                    )
                )

            elif role == "Admin":

                user.is_staff = True
                user.is_superuser = False
                user.set_password(password)

                user.groups.clear()
                user.groups.add(
                    groups["Admin"]
                )

                user.user_permissions.set(
                    admin_permissions
                )

                user.save()

                self.stdout.write(
                    self.style.SUCCESS(
                        "Admin 'jane@admin' configured."
                    )
                )

            elif role == "Manager":

                user.is_staff = False
                user.is_superuser = False
                user.set_password(password)

                user.groups.clear()
                user.groups.add(
                    groups["Manager"]
                )

                user.user_permissions.set(
                    manager_permissions
                )

                user.save()

                self.stdout.write(
                    self.style.SUCCESS(
                        "Manager 'jane' configured."
                    )
                )

            elif role == "Viewer":

                user.is_staff = False
                user.is_superuser = False
                user.set_password(password)

                user.groups.clear()
                user.groups.add(
                    groups["Viewer"]
                )

                user.user_permissions.set(
                    viewer_permissions
                )

                user.save()

                self.stdout.write(
                    self.style.SUCCESS(
                        "Viewer 'eliusa' configured."
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                "All four users configured successfully."
            )
        )