import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.models import AbstractUser
from django.db import connections, models, router, transaction
from django.utils.crypto import salted_hmac
from django.utils import timezone

from apps.companies.constants import (
    LANGUAGE_SW,
    SUPPORTED_LANGUAGE_CHOICES,
)


class User(AbstractUser):
    class ThemePreference(models.TextChoices):
        SYSTEM = "SYSTEM", "System"
        LIGHT = "LIGHT", "Light"
        DARK = "DARK", "Dark"

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    email = models.EmailField(
        unique=True,
    )

    phone = models.CharField(
        max_length=32,
        blank=True,
        db_index=True,
    )
    preferred_language = models.CharField(
        max_length=10,
        choices=SUPPORTED_LANGUAGE_CHOICES,
        default=LANGUAGE_SW,
    )
    theme_preference = models.CharField(
        max_length=10,
        choices=ThemePreference.choices,
        default=ThemePreference.SYSTEM,
    )
    profile_photo = models.ImageField(
        upload_to="profiles/%Y/%m/",
        blank=True,
    )

    is_platform_admin = models.BooleanField(
        default=False,
        db_index=True,
    )
    must_change_password = models.BooleanField(
        default=False,
        db_index=True,
    )
    password_changed_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    session_revocation_nonce = models.UUIDField(null=True, blank=True, editable=False)

    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def _get_session_auth_hash(self, secret=None):
        self._require_session_revocation_guard()
        if self.session_revocation_nonce is None:
            return super()._get_session_auth_hash(secret=secret)
        return salted_hmac(
            "django.contrib.auth.models.AbstractBaseUser.get_session_auth_hash",
            f"{self.password}:{self.session_revocation_nonce}",
            secret=secret, algorithm="sha256",
        ).hexdigest()

    def _require_session_revocation_guard(self):
        if self.pk is None:
            return
        using = self._state.db or router.db_for_read(type(self), instance=self)
        connection = connections[using]
        table = type(self)._meta.db_table
        name = 'bizmanager_user_nonce_guard'
        with connection.cursor() as cursor:
            if connection.vendor == 'sqlite':
                cursor.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = %s AND tbl_name = %s",
                    [name, table],
                )
                q = connection.ops.quote_name
                quoted_table = q(table)
                pk = q(self._meta.pk.column)
                active = q(self._meta.get_field('is_active').column)
                nonce = q(self._meta.get_field('session_revocation_nonce').column)
                expected = (
                    f'CREATE TRIGGER {q(name)} AFTER UPDATE OF {active} ON {quoted_table} '
                    f'FOR EACH ROW WHEN OLD.{active} = 1 AND NEW.{active} = 0 '
                    f'AND NEW.{nonce} IS OLD.{nonce} BEGIN '
                    f'UPDATE {quoted_table} SET {nonce} = lower(hex(randomblob(16))) '
                    f'WHERE {pk} = NEW.{pk}; END'
                )
                row = cursor.fetchone()
                if row is None or row[0] != expected:
                    raise PermissionDenied('Account security guard is unavailable.')
                return
            elif connection.vendor == 'postgresql':
                cursor.execute(
                    "SELECT 1 FROM pg_catalog.pg_trigger WHERE tgname = %s "
                    "AND tgrelid = pg_catalog.to_regclass(%s) AND NOT tgisinternal "
                    "AND tgenabled IN ('O', 'A')",
                    [name, table],
                )
            else:
                raise PermissionDenied('Account security guard is unavailable.')
            if cursor.fetchone() is None:
                raise PermissionDenied('Account security guard is unavailable.')

    def save(self, *args, **kwargs):
        fields = kwargs.get("update_fields")
        if fields is not None and not fields:
            return super().save(*args, **kwargs)
        if fields is not None and "session_revocation_nonce" in fields:
            raise ValidationError("Session revocation nonce is system managed.")
        if self.pk is None:
            return super().save(*args, **kwargs)
        using = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            current = (type(self)._base_manager.using(using).select_for_update()
                .filter(pk=self.pk).values("is_active", "session_revocation_nonce").first())
            if current is not None:
                status_write = fields is None or "is_active" in fields
                if status_write and not isinstance(self.is_active, bool):
                    raise ValidationError("Account status must be a boolean.")
                revoked = status_write and current["is_active"] and not self.is_active
                self.session_revocation_nonce = uuid.uuid4() if revoked else current["session_revocation_nonce"]
                if revoked and fields is not None:
                    kwargs["update_fields"] = set(fields) | {"session_revocation_nonce"}
            return super().save(*args, **kwargs)

    class Meta:
        indexes = [
            models.Index(
                fields=("is_active", "is_platform_admin"),
                name="user_active_platform_idx",
            ),
        ]

    def has_perm(self, perm, obj=None):
        """
        Company-owned business permissions are always company-context aware.

        This override intentionally prevents Django's normal superuser
        shortcut from giving a Platform Administrator cross-company
        business access when no company has been selected.
        """
        if perm.startswith(("pigs.", "transport.")):
            from apps.companies.scope import get_current_company

            if not self.is_active:
                return False

            if self.is_platform_admin:
                return False

            if get_current_company() is None:
                return False

        return super().has_perm(
            perm,
            obj=obj,
        )

    def has_module_perms(self, app_label):
        if app_label in {"pigs", "transport"}:
            from apps.companies.scope import get_current_company

            if not self.is_active:
                return False

            if self.is_platform_admin:
                return False

            if get_current_company() is None:
                return False

        return super().has_module_perms(
            app_label
        )

    def __str__(self):
        return self.username


class ImmutableUserAuditQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError(
            "Audit logs are append-only and cannot be deleted."
        )

    def update(self, **kwargs):
        raise ValidationError(
            "Audit logs are append-only and cannot be modified."
        )


class ImmutableUserAuditManager(models.Manager):
    def get_queryset(self):
        return ImmutableUserAuditQuerySet(
            self.model,
            using=self._db,
        )


class AuditLog(models.Model):
    ACTION_CHOICES = [
        ("LOGIN", "Login"),
        ("LOGOUT", "Logout"),
        ("CREATE", "Create"),
        ("VIEW", "View"),
        ("EDIT", "Edit"),
        ("DELETE", "Delete"),
        ("PERMISSION", "Permission"),
        ("SECURITY", "Security"),
        ("APPROVAL", "Approval"),
        ("SUSPEND", "Suspend"),
        ("ARCHIVE", "Archive"),
    ]

    RESULT_CHOICES = [
        ("SUCCESS", "Success"),
        ("FAILURE", "Failure"),
        ("DENIED", "Denied"),
    ]

    event_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(
        max_length=20,
        choices=ACTION_CHOICES,
        db_index=True,
    )
    result = models.CharField(
        max_length=20,
        choices=RESULT_CHOICES,
        default="SUCCESS",
        db_index=True,
    )
    model_name = models.CharField(
        max_length=100,
        blank=True,
    )
    object_id = models.CharField(
        max_length=100,
        blank=True,
    )
    company_public_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
    )
    description = models.TextField(
        blank=True,
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
    )
    user_agent = models.CharField(
        max_length=500,
        blank=True,
    )
    timestamp = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    objects = ImmutableUserAuditManager()

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(
                fields=("user", "timestamp"),
                name="audit_user_time_idx",
            ),
            models.Index(
                fields=("action", "timestamp"),
                name="audit_action_time_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(
                "Audit logs are immutable."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Audit logs are append-only and cannot be deleted."
        )


class SSOIdentity(models.Model):
    """
    Dormant future CentralAuth mapping.
    No password or client secret is stored here.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sso_identity",
    )
    central_subject = models.UUIDField(
        unique=True,
    )
    central_username = models.CharField(
        max_length=150,
    )
    central_email = models.EmailField(
        blank=True,
    )
    last_synced_at = models.DateTimeField(
        auto_now=True,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    def __str__(self):
        return f"{self.central_username} -> {self.user.username}"


class GoogleIdentity(models.Model):
    """Verified Google OpenID subject linked to one local PIGS account."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="google_identity",
    )
    subject = models.CharField(
        max_length=255,
        unique=True,
    )
    email_at_link = models.EmailField()
    display_name = models.CharField(
        max_length=255,
        blank=True,
    )
    picture_url = models.URLField(
        max_length=500,
        blank=True,
    )
    last_login_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Google {self.subject} -> {self.user.username}"


class PasswordRecoveryChallenge(models.Model):
    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="password_recovery_challenges",
    )
    code_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True, db_index=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    resend_count = models.PositiveSmallIntegerField(default=0)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    browser_key_hash = models.CharField(max_length=64, db_index=True)
    request_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("user", "created_at"), name="recovery_user_time_idx"),
            models.Index(fields=("expires_at", "used_at"), name="recovery_expiry_used_idx"),
        ]

    def __str__(self):
        return f"Password recovery for {self.user_id} at {self.created_at}"


class PasswordRecoveryThrottle(models.Model):
    key_hash = models.CharField(max_length=64, unique=True)
    failed_attempts = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True, db_index=True)

    def is_locked(self, *, now=None):
        now = now or timezone.now()
        return bool(self.locked_until and self.locked_until > now)

    def __str__(self):
        return f"Recovery throttle {self.key_hash[:12]} ({self.failed_attempts})"
