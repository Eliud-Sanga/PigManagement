import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .constants import LANGUAGE_SW, SUPPORTED_LANGUAGE_CHOICES


def default_onboarding_languages():
    return ["sw", "en"]


class CompanyOwnerInvitation(models.Model):
    class Status(models.TextChoices):
        SENT = "SENT", "Sent"
        OPENED = "OPENED", "Opened"
        SUBMITTED = "SUBMITTED", "Submitted"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        COMPLETED = "COMPLETED", "Completed"
        EXPIRED = "EXPIRED", "Expired"
        REVOKED = "REVOKED", "Revoked"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    email = models.EmailField(db_index=True)
    token_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SENT,
        db_index=True,
    )
    expires_at = models.DateTimeField(db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="company_owner_invitations_created",
    )
    sent_at = models.DateTimeField(default=timezone.now)
    last_sent_at = models.DateTimeField(default=timezone.now)
    resend_count = models.PositiveSmallIntegerField(default=0)
    opened_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    business_type = models.CharField(max_length=30, default="pig")
    transport_cargo_enabled = models.BooleanField(default=False)
    transport_passenger_enabled = models.BooleanField(default=False)
    company_name = models.CharField(max_length=160, blank=True)
    company_code = models.SlugField(max_length=50, blank=True)
    company_email = models.EmailField(blank=True)
    company_phone = models.CharField(max_length=40, blank=True)
    company_address = models.TextField(blank=True)
    company_timezone = models.CharField(
        max_length=64,
        default="Africa/Dar_es_Salaam",
    )
    allowed_languages = models.JSONField(default=default_onboarding_languages)
    default_language = models.CharField(
        max_length=10,
        choices=SUPPORTED_LANGUAGE_CHOICES,
        default=LANGUAGE_SW,
    )
    submitted_ip = models.GenericIPAddressField(null=True, blank=True)
    decision_reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="company_owner_invitations_reviewed",
    )
    continuation_token_hash = models.CharField(max_length=64, blank=True)
    continuation_expires_at = models.DateTimeField(null=True, blank=True)
    continuation_last_sent_at = models.DateTimeField(null=True, blank=True)
    continuation_resend_count = models.PositiveSmallIntegerField(default=0)
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="completed_company_owner_invitations",
    )
    provisioned_company = models.ForeignKey(
        "companies.Company",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="onboarding_invitations",
    )
    login_now_token_hash = models.CharField(max_length=64, blank=True)
    login_now_expires_at = models.DateTimeField(null=True, blank=True)
    login_now_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("email", "status"), name="invite_email_status_idx"),
            models.Index(fields=("status", "expires_at"), name="invite_status_expiry_idx"),
        ]

    def is_expired(self, *, now=None):
        now = now or timezone.now()
        return self.expires_at <= now

    def clean(self):
        super().clean()
        self.email = (self.email or "").strip().lower()
        self.company_name = (self.company_name or "").strip()
        self.company_code = (self.company_code or "").strip().lower()
        if self.business_type not in {"pig", "transport"}:
            raise ValidationError({"business_type": "Unsupported business type."})
        if self.business_type == "transport" and not (self.transport_cargo_enabled or self.transport_passenger_enabled):
            raise ValidationError("At least one Transport service must be selected.")

    def __str__(self):
        return f"{self.email} ({self.status})"


class ImmutableOnboardingEventQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("Onboarding audit events are append-only.")

    def update(self, **kwargs):
        raise ValidationError("Onboarding audit events cannot be modified.")


class ImmutableOnboardingEventManager(models.Manager):
    def get_queryset(self):
        return ImmutableOnboardingEventQuerySet(self.model, using=self._db)


class CompanyOnboardingEvent(models.Model):
    invitation = models.ForeignKey(
        CompanyOwnerInvitation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="company_onboarding_events",
    )
    action = models.CharField(max_length=60, db_index=True)
    result = models.CharField(max_length=20, default="SUCCESS")
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = ImmutableOnboardingEventManager()

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("action", "created_at"), name="onboard_action_time_idx"),
            models.Index(fields=("invitation", "created_at"), name="onboard_invite_time_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("Onboarding audit events are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Onboarding audit events are append-only.")


class CompanyInvitationThrottle(models.Model):
    key_hash = models.CharField(max_length=64, unique=True)
    failed_attempts = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True, db_index=True)

    def is_locked(self, *, now=None):
        now = now or timezone.now()
        return bool(self.locked_until and self.locked_until > now)
