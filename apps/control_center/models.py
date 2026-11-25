import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class ImmutableAuditQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError(
            "Security audit events are append-only and cannot be deleted."
        )

    def update(self, **kwargs):
        raise ValidationError(
            "Security audit events are append-only and cannot be modified."
        )


class ImmutableAuditManager(models.Manager):
    def get_queryset(self):
        return ImmutableAuditQuerySet(
            self.model,
            using=self._db,
        )


class SecuritySettings(models.Model):
    """
    Singleton security policy for this PIGS installation.

    Every Admin save is additionally protected at the view layer by
    re-entering the Platform Administrator password.
    """

    singleton_key = models.PositiveSmallIntegerField(
        default=1,
        unique=True,
        editable=False,
    )

    max_failed_login_attempts = models.PositiveSmallIntegerField(
        default=3,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(20),
        ],
    )

    failed_attempt_window_minutes = models.PositiveIntegerField(
        default=15,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(1440),
        ],
    )

    login_lock_minutes = models.PositiveIntegerField(
        default=30,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(10080),
        ],
    )

    device_cookie_days = models.PositiveIntegerField(
        default=365,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(3650),
        ],
    )

    idle_timeout_minutes = models.PositiveIntegerField(
        default=30,
        validators=[
            MinValueValidator(5),
            MaxValueValidator(1440),
        ],
        help_text=(
            "Logout an authenticated user after this many minutes "
            "without an application request."
        ),
    )

    absolute_session_timeout_minutes = models.PositiveIntegerField(
        default=720,
        validators=[
            MinValueValidator(30),
            MaxValueValidator(10080),
        ],
        help_text=(
            "Maximum authenticated session lifetime even while the "
            "user remains active."
        ),
    )

    maintenance_enabled = models.BooleanField(
        default=False,
    )

    maintenance_starts_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    maintenance_ends_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    maintenance_message = models.CharField(
        max_length=500,
        blank=True,
        default=(
            "Mfumo uko kwenye matengenezo. "
            "Tafadhali jaribu tena baada ya muda uliowekwa."
        ),
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_settings_updates",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        verbose_name = "Security settings"
        verbose_name_plural = "Security settings"

    def clean(self):
        super().clean()

        if (
            self.absolute_session_timeout_minutes
            < self.idle_timeout_minutes
        ):
            raise ValidationError(
                {
                    "absolute_session_timeout_minutes": (
                        "Maximum session lifetime must be greater than "
                        "or equal to the idle timeout."
                    )
                }
            )

        if (
            self.maintenance_starts_at
            and self.maintenance_ends_at
            and self.maintenance_ends_at
            <= self.maintenance_starts_at
        ):
            raise ValidationError(
                {
                    "maintenance_ends_at":
                        "Maintenance end must be after the start time."
                }
            )

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(
            singleton_key=1,
        )
        return obj

    def maintenance_is_active(self, *, now=None):
        if not self.maintenance_enabled:
            return False

        now = now or timezone.now()

        if (
            self.maintenance_starts_at
            and now < self.maintenance_starts_at
        ):
            return False

        if (
            self.maintenance_ends_at
            and now >= self.maintenance_ends_at
        ):
            return False

        return True

    def __str__(self):
        return "PIGS Security Settings"


class BrowserLoginThrottle(models.Model):
    browser_key_hash = models.CharField(
        max_length=64,
        unique=True,
    )

    failed_attempts = models.PositiveIntegerField(
        default=0,
    )

    locked_until = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    last_failed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    last_identifier_hash = models.CharField(
        max_length=64,
        blank=True,
    )

    last_ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def is_locked(self, *, now=None):
        now = now or timezone.now()

        return bool(
            self.locked_until
            and self.locked_until > now
        )

    def __str__(self):
        return (
            f"Browser {self.browser_key_hash[:12]} "
            f"({self.failed_attempts} failures)"
        )


class AccountLoginThrottle(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="login_throttle",
    )

    failed_attempts = models.PositiveIntegerField(
        default=0,
    )

    locked_until = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    last_failed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    last_ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def is_locked(self, *, now=None):
        now = now or timezone.now()

        return bool(
            self.locked_until
            and self.locked_until > now
        )

    def __str__(self):
        return (
            f"{self.user.username} "
            f"({self.failed_attempts} failures)"
        )


class SecurityAuditEvent(models.Model):
    class Category(models.TextChoices):
        AUTHENTICATION = "AUTH", "Authentication"
        SECURITY = "SECURITY", "Security"
        ADMIN = "ADMIN", "Administration"
        IDENTITY = "IDENTITY", "Identity"
        MAINTENANCE = "MAINTENANCE", "Maintenance"

    class Result(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        FAILURE = "FAILURE", "Failure"
        DENIED = "DENIED", "Denied"
        BLOCKED = "BLOCKED", "Blocked"

    class Severity(models.TextChoices):
        INFO = "INFO", "Info"
        WARNING = "WARNING", "Warning"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    event_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )

    occurred_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        editable=False,
    )

    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        db_index=True,
    )

    action = models.CharField(
        max_length=80,
        db_index=True,
    )

    result = models.CharField(
        max_length=20,
        choices=Result.choices,
        db_index=True,
    )

    severity = models.CharField(
        max_length=20,
        choices=Severity.choices,
        default=Severity.INFO,
        db_index=True,
    )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_audit_events_as_actor",
    )

    subject_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_audit_events_as_subject",
    )

    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_audit_events",
    )

    actor_identifier = models.CharField(
        max_length=254,
        blank=True,
    )

    subject_identifier = models.CharField(
        max_length=254,
        blank=True,
    )

    attempted_identifier = models.CharField(
        max_length=254,
        blank=True,
    )

    reason_code = models.CharField(
        max_length=80,
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

    browser_key_hash = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
    )

    request_path = models.CharField(
        max_length=300,
        blank=True,
    )

    request_method = models.CharField(
        max_length=10,
        blank=True,
    )

    previous_hash = models.CharField(
        max_length=64,
        blank=True,
        editable=False,
    )

    event_hash = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
    )

    objects = ImmutableAuditManager()

    class Meta:
        ordering = ("-occurred_at", "-id")
        indexes = [
            models.Index(
                fields=("category", "occurred_at"),
                name="secaudit_category_time_idx",
            ),
            models.Index(
                fields=("result", "occurred_at"),
                name="secaudit_result_time_idx",
            ),
            models.Index(
                fields=("severity", "occurred_at"),
                name="secaudit_severity_time_idx",
            ),
            models.Index(
                fields=("actor", "occurred_at"),
                name="secaudit_actor_time_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(
                "Security audit events are immutable."
            )

        if not self.event_hash:
            raise ValidationError(
                "Security audit events must be written "
                "through the append-only audit service."
            )

        return super().save(
            *args,
            **kwargs,
        )

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Security audit events are append-only "
            "and cannot be deleted."
        )

    def __str__(self):
        return (
            f"{self.occurred_at:%Y-%m-%d %H:%M:%S} "
            f"{self.action} {self.result}"
        )
