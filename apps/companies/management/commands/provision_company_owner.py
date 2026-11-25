from getpass import getpass
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.companies.constants import ROLE_OWNER
from apps.companies.models import Company, CompanyAuditEvent, CompanyMembership
from apps.companies.services import ensure_default_roles


User = get_user_model()


class Command(BaseCommand):
    help = (
        "Create or activate a regular Company Owner account without granting "
        "Platform Administrator business-plane access."
    )

    def add_arguments(self, parser):
        parser.add_argument("--list-companies", action="store_true")
        parser.add_argument(
            "--company",
            help="Company code or public UUID. Required unless only one active company exists.",
        )
        parser.add_argument(
            "--actor",
            help="Active Platform Administrator username. Auto-resolved when unique.",
        )
        parser.add_argument("--username")
        parser.add_argument("--email")
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument(
            "--password-stdin",
            action="store_true",
            help="Read the new password from stdin instead of a hidden prompt.",
        )
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help="Replace the password when the regular user already exists.",
        )

    def handle(self, *args, **options):
        if options["list_companies"]:
            companies = Company.objects.order_by("name")
            if not companies.exists():
                self.stdout.write("No companies found.")
                return
            for company in companies:
                self.stdout.write(
                    f"{company.code}\t{company.public_id}\t{company.status}\t{company.name}"
                )
            return

        company = self._company(options.get("company"))
        actor = self._actor(options.get("actor"))
        username = (options.get("username") or "").strip()
        email = (options.get("email") or "").strip().lower()
        if not username or not email:
            raise CommandError("--username and --email are required.")

        user = User.objects.filter(
            Q(username__iexact=username) | Q(email__iexact=email)
        ).first()
        creating = user is None
        if user is not None and user.is_platform_admin:
            raise CommandError(
                "A Platform Administrator cannot also be provisioned as a Company Owner. "
                "Choose a distinct username/email."
            )
        if user is not None and (
            user.username.casefold() != username.casefold()
            or user.email.casefold() != email.casefold()
        ):
            raise CommandError(
                "Username or email belongs to another local account. Use that account's exact values."
            )

        if user is not None and not user.is_active:
            raise CommandError(
                "This account is inactive. Reactivate it through the authorized "
                "account-status workflow before provisioning an owner."
            )

        password = None
        if creating or options["reset_password"]:
            password = self._password(options["password_stdin"])
            probe = user or User(username=username, email=email)
            try:
                validate_password(password, user=probe)
            except ValidationError as error:
                raise CommandError(" ".join(error.messages)) from error

        with transaction.atomic():
            actor = User.objects.select_for_update().get(pk=actor.pk)
            if not actor.is_active or not actor.is_platform_admin:
                raise CommandError("Platform Administrator is no longer active.")
            company = Company.objects.select_for_update().get(pk=company.pk)
            if company.status != Company.Status.ACTIVE:
                raise CommandError("Company is no longer active.")
            if creating:
                user = User(
                    username=username,
                    email=email,
                    first_name=(options.get("first_name") or "").strip(),
                    last_name=(options.get("last_name") or "").strip(),
                    is_active=True,
                    is_staff=False,
                    is_superuser=False,
                    is_platform_admin=False,
                    must_change_password=False,
                    password_changed_at=timezone.now(),
                )
                user.set_password(password)
                user.full_clean()
                user.save()
            else:
                # Recheck under lock: a global status change may have occurred
                # after the first lookup and before this transaction started.
                user = User.objects.select_for_update().get(pk=user.pk)
                if not user.is_active:
                    raise CommandError("This account is inactive. Reactivate it through the authorized account-status workflow before provisioning an owner.")
                if user.is_platform_admin:
                    raise CommandError("A Platform Administrator cannot also be provisioned as a Company Owner.")
                if user.username.casefold() != username.casefold() or user.email.casefold() != email.casefold():
                    raise CommandError("Account identity changed. Reload before provisioning.")
                changed = []
                first_name = (options.get("first_name") or "").strip()
                last_name = (options.get("last_name") or "").strip()
                if first_name and user.first_name != first_name:
                    user.first_name = first_name
                    changed.append("first_name")
                if last_name and user.last_name != last_name:
                    user.last_name = last_name
                    changed.append("last_name")
                if password is not None:
                    user.set_password(password)
                    user.password_changed_at = timezone.now()
                    user.must_change_password = False
                    changed.extend(
                        ["password", "password_changed_at", "must_change_password"]
                    )
                if changed:
                    user.save(update_fields=[*dict.fromkeys(changed), "updated_at"])

            roles = ensure_default_roles(company)
            membership, membership_created = CompanyMembership.objects.get_or_create(
                company=company,
                user=user,
                defaults={
                    "role": roles[ROLE_OWNER],
                    "status": CompanyMembership.Status.ACTIVE,
                    "requested_language": company.default_language,
                    "approved_language": company.default_language,
                    "created_by": actor,
                    "approved_by": actor,
                    "approved_at": timezone.now(),
                },
            )
            if not membership_created:
                membership.role = roles[ROLE_OWNER]
                membership.status = CompanyMembership.Status.ACTIVE
                membership.requested_language = company.default_language
                membership.approved_language = company.default_language
                membership.approved_by = actor
                membership.approved_at = timezone.now()
                membership.full_clean()
                membership.save(
                    update_fields=[
                        "role",
                        "status",
                        "requested_language",
                        "approved_language",
                        "approved_by",
                        "approved_at",
                        "updated_at",
                    ]
                )

            CompanyAuditEvent.objects.create(
                company=company,
                actor=actor,
                action="OWNER_PROVISIONED",
                description=f"Company Owner account {user.username} provisioned.",
                metadata={
                    "owner_user_id": user.pk,
                    "membership_id": membership.pk,
                    "created_user": creating,
                    "reset_password": bool(options["reset_password"]),
                },
            )

        self.stdout.write(self.style.SUCCESS("Company Owner provisioned safely."))
        self.stdout.write(f"Company: {company.code} — {company.name}")
        self.stdout.write(f"Username: {user.username}")
        self.stdout.write(f"Email: {user.email}")
        self.stdout.write("Platform Admin status: false")

    def _company(self, value):
        queryset = Company.objects.filter(status=Company.Status.ACTIVE)
        if value:
            normalized = value.strip()
            company = queryset.filter(code__iexact=normalized).first()
            if company is None:
                try:
                    public_id = uuid.UUID(normalized)
                except ValueError:
                    public_id = None
                if public_id is not None:
                    company = queryset.filter(public_id=public_id).first()
            if company is None:
                raise CommandError("Active company was not found.")
            return company
        count = queryset.count()
        if count != 1:
            raise CommandError(
                "Use --company CODE (or --list-companies) because the active company is not unique."
            )
        return queryset.get()

    def _actor(self, value):
        queryset = User.objects.filter(is_active=True, is_platform_admin=True)
        if value:
            actor = queryset.filter(username__iexact=value.strip()).first()
            if actor is None:
                raise CommandError("Active Platform Administrator actor was not found.")
            return actor
        if queryset.count() != 1:
            raise CommandError(
                "Use --actor USERNAME because the active Platform Administrator is not unique."
            )
        return queryset.get()

    def _password(self, from_stdin):
        if from_stdin:
            import sys

            password = sys.stdin.readline().rstrip("\r\n")
        else:
            password = getpass("New Company Owner password: ")
            confirmation = getpass("Confirm password: ")
            if password != confirmation:
                raise CommandError("Passwords do not match.")
        if not password:
            raise CommandError("Password cannot be empty.")
        return password
