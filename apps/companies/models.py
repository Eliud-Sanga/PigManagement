import uuid

from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from .scope import get_current_company

from .constants import (
    DEFAULT_COMPANY_LANGUAGES,
    LANGUAGE_SW,
    SUPPORTED_LANGUAGE_CHOICES,
    SUPPORTED_LANGUAGE_CODES,
)


def default_allowed_languages():
    return list(DEFAULT_COMPANY_LANGUAGES)


class CompanyQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=Company.Status.ACTIVE)


class Company(models.Model):
    class BusinessType(models.TextChoices):
        PIG = "pig", "Pig Business"
        TRANSPORT = "transport", "Transport"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        SUSPENDED = "SUSPENDED", "Suspended"
        ARCHIVED = "ARCHIVED", "Archived"

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    code = models.SlugField(
        max_length=50,
        unique=True,
    )
    name = models.CharField(
        max_length=160,
    )
    business_type = models.CharField(
        max_length=30,
        choices=BusinessType.choices,
        default=BusinessType.PIG,
        db_index=True,
    )
    email = models.EmailField(
        blank=True,
    )
    phone = models.CharField(
        max_length=40,
        blank=True,
    )
    address = models.TextField(
        blank=True,
    )
    logo = models.ImageField(
        upload_to="companies/logos/%Y/%m/",
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    allowed_languages = models.JSONField(
        default=default_allowed_languages,
    )
    default_language = models.CharField(
        max_length=10,
        choices=SUPPORTED_LANGUAGE_CHOICES,
        default=LANGUAGE_SW,
    )
    timezone = models.CharField(
        max_length=64,
        default="Africa/Dar_es_Salaam",
    )
    owner_user_management_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Allow the Company Owner to create and remove "
            "users inside this company. Platform Admin "
            "governance is always retained."
        ),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="companies_created",
    )
    suspended_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    archived_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    objects = CompanyQuerySet.as_manager()

    class Meta:
        ordering = ("name",)
        indexes = [
            models.Index(
                fields=("status", "name"),
                name="company_status_name_idx",
            ),
        ]

    def clean(self):
        super().clean()

        if self.business_type not in self.BusinessType.values:
            raise ValidationError({"business_type": "Unsupported business type."})

        if not isinstance(self.allowed_languages, list) or not self.allowed_languages:
            raise ValidationError(
                {"allowed_languages": "At least one language is required."}
            )

        normalized = []

        for value in self.allowed_languages:
            code = str(value).strip().lower()

            if code not in SUPPORTED_LANGUAGE_CODES:
                raise ValidationError(
                    {"allowed_languages": f"Unsupported language: {value}."}
                )

            if code not in normalized:
                normalized.append(code)

        self.allowed_languages = normalized

        if self.default_language not in normalized:
            raise ValidationError(
                {
                    "default_language":
                        "Default language must be enabled for the company."
                }
            )

    @property
    def is_operational(self):
        return self.status == self.Status.ACTIVE

    def language_is_allowed(self, language_code):
        return (
            str(language_code).strip().lower()
            in set(self.allowed_languages or [])
        )

    def __str__(self):
        return self.name


class CompanyRole(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="roles",
    )
    code = models.CharField(
        max_length=50,
    )
    name = models.CharField(
        max_length=100,
    )
    description = models.TextField(
        blank=True,
    )
    is_reserved = models.BooleanField(
        default=False,
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )
    permissions = models.ManyToManyField(
        Permission,
        blank=True,
        related_name="company_roles",
        limit_choices_to={
            "content_type__app_label__in": [
                "pigs",
                "transport",
                "companies",
            ],
        },
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ("company_id", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("company", "code"),
                name="uniq_company_role_code",
            ),
            models.UniqueConstraint(
                fields=("company", "name"),
                name="uniq_company_role_name",
            ),
        ]
        indexes = [
            models.Index(
                fields=("company", "is_active"),
                name="role_company_active_idx",
            ),
        ]

    def clean(self):
        super().clean()
        self.code = (self.code or "").strip().upper()

    def __str__(self):
        return f"{self.company.code}:{self.name}"


class CompanyMembershipQuerySet(models.QuerySet):
    def active(self):
        return self.filter(
            status=CompanyMembership.Status.ACTIVE,
            company__status=Company.Status.ACTIVE,
            role__is_active=True,
        )


class CompanyMembership(models.Model):
    class Status(models.TextChoices):
        PENDING_APPROVAL = "PENDING_APPROVAL", "Pending approval"
        ACTIVE = "ACTIVE", "Active"
        SUSPENDED = "SUSPENDED", "Suspended"
        REJECTED = "REJECTED", "Rejected"
        ARCHIVED = "ARCHIVED", "Archived"

    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="company_memberships",
    )
    role = models.ForeignKey(
        CompanyRole,
        on_delete=models.PROTECT,
        related_name="memberships",
    )

    # Permission overrides belong to this membership, not to the User
    # globally. The same person can therefore have different authority
    # in different companies without changing the shared role template.
    granted_permissions = models.ManyToManyField(
        Permission,
        blank=True,
        related_name="company_membership_grants",
        limit_choices_to={
            "content_type__app_label__in": [
                "pigs",
                "transport",
                "companies",
            ],
        },
    )

    denied_permissions = models.ManyToManyField(
        Permission,
        blank=True,
        related_name="company_membership_denials",
        limit_choices_to={
            "content_type__app_label__in": [
                "pigs",
                "transport",
                "companies",
            ],
        },
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING_APPROVAL,
        db_index=True,
    )
    requested_language = models.CharField(
        max_length=10,
        choices=SUPPORTED_LANGUAGE_CHOICES,
        default=LANGUAGE_SW,
    )
    approved_language = models.CharField(
        max_length=10,
        choices=SUPPORTED_LANGUAGE_CHOICES,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="company_memberships_created",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="company_memberships_approved",
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    objects = CompanyMembershipQuerySet.as_manager()

    class Meta:
        permissions = [
            (
                "manage_company_users",
                "Can create and manage users inside a company",
            ),
            (
                "manage_company_roles",
                "Can manage company role permission templates",
            ),
            (
                "manage_member_permissions",
                "Can manage per-member permission overrides",
            ),
        ]

        constraints = [
            models.UniqueConstraint(
                fields=("company", "user"),
                name="uniq_company_user_membership",
            ),
        ]
        indexes = [
            models.Index(
                fields=("user", "status"),
                name="member_user_status_idx",
            ),
            models.Index(
                fields=("company", "status"),
                name="member_company_status_idx",
            ),
            models.Index(
                fields=("company", "role"),
                name="member_company_role_idx",
            ),
        ]

    def clean(self):
        super().clean()

        if self.role_id and self.company_id:
            if self.role.company_id != self.company_id:
                raise ValidationError(
                    {"role": "Role belongs to another company."}
                )

        if self.company_id and not self.company.language_is_allowed(
            self.requested_language
        ):
            raise ValidationError(
                {
                    "requested_language":
                        "Requested language is not enabled for this company."
                }
            )

        if (
            self.approved_language
            and self.company_id
            and not self.company.language_is_allowed(self.approved_language)
        ):
            raise ValidationError(
                {
                    "approved_language":
                        "Approved language is not enabled for this company."
                }
            )

        if (
            self.status == self.Status.ACTIVE
            and not self.approved_language
        ):
            raise ValidationError(
                {
                    "approved_language":
                        "Active membership requires an approved language."
                }
            )

    def __str__(self):
        return (
            f"{self.user.username} -> "
            f"{self.company.code} ({self.role.code})"
        )



class CompanyUserActivation(models.Model):
    public_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    membership = models.OneToOneField(
        CompanyMembership,
        on_delete=models.PROTECT,
        related_name="user_activation",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="company_user_activations",
    )
    token_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField(db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )
    resend_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("expires_at", "used_at"),
                name="cmpact_exp_used_idx",
            ),
            models.Index(
                fields=("user", "used_at"),
                name="cmpact_user_used_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.membership_id
            and self.user_id
            and self.membership.user_id != self.user_id
        ):
            raise ValidationError(
                {"user": "Activation user must match membership user."}
            )

    def __str__(self):
        return f"Activation {self.public_id} for {self.user.username}"


class ImmutableCompanyAuditQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError(
            "Company audit events are append-only and cannot be deleted."
        )

    def update(self, **kwargs):
        raise ValidationError(
            "Company audit events are append-only and cannot be modified."
        )


class ImmutableCompanyAuditManager(models.Manager):
    def get_queryset(self):
        return ImmutableCompanyAuditQuerySet(
            self.model,
            using=self._db,
        )


class CompanyAuditEvent(models.Model):
    event_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="company_audit_events",
    )
    action = models.CharField(
        max_length=80,
        db_index=True,
    )
    result = models.CharField(
        max_length=20,
        default="SUCCESS",
        db_index=True,
    )
    description = models.TextField(
        blank=True,
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    objects = ImmutableCompanyAuditManager()

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("company", "created_at"),
                name="cmpaudit_company_time_idx",
            ),
            models.Index(
                fields=("actor", "created_at"),
                name="cmpaudit_actor_time_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(
                "Company audit events are immutable."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Company audit events are append-only and cannot be deleted."
        )


class CompanyScopeError(ValidationError):
    """Raised when company-owned data is accessed without a tenant scope."""


class CompanyScopedQuerySet(models.QuerySet):
    def for_company(
        self,
        company,
        *,
        include_archived=False,
    ):
        if company is None:
            return self.none()

        queryset = self.filter(
            company=company
        )

        if (
            not include_archived
            and any(
                field.name == "is_archived"
                for field in self.model._meta.fields
            )
        ):
            queryset = queryset.filter(
                is_archived=False
            )

        return queryset

    def delete(self):
        """
        Archive row-by-row so model-specific safety rules cannot be bypassed.

        Destructive operations are rare; correctness and auditability are
        more important here than bulk-delete speed.
        """

        total = 0
        details = {}

        with transaction.atomic():
            for obj in self.select_for_update():
                count, object_details = obj.delete()

                total += count

                for label, value in object_details.items():
                    details[label] = (
                        details.get(label, 0)
                        + value
                    )

        return (
            total,
            details,
        )

    def hard_delete(self):
        return super().delete()


class CompanyScopedManager(models.Manager):
    """
    Safe-by-default manager for company-owned business data.

    Normal application code only sees the currently selected company.
    Governance/recovery code must deliberately use ``all_objects``.
    """

    def _scope_company(self):
        return get_current_company()

    def get_queryset(self):
        queryset = CompanyScopedQuerySet(
            self.model,
            using=self._db,
        )

        company = self._scope_company()

        if company is None:
            return queryset.none()

        queryset = queryset.filter(
            company_id=company.pk
        )

        if any(
            field.name == "is_archived"
            for field in self.model._meta.fields
        ):
            queryset = queryset.filter(
                is_archived=False
            )

        return queryset

    def for_company(
        self,
        company,
        *,
        include_archived=False,
    ):
        return CompanyScopedQuerySet(
            self.model,
            using=self._db,
        ).for_company(
            company,
            include_archived=include_archived,
        )

    def _company_from_values(self, values):
        explicit = values.get("company")
        current = self._scope_company()

        inferred = []

        for value in values.values():
            company_id = getattr(
                value,
                "company_id",
                None,
            )

            if company_id:
                inferred.append(
                    getattr(
                        value,
                        "company",
                        None,
                    )
                )

        inferred = [
            item
            for item in inferred
            if item is not None
        ]

        company = (
            explicit
            or current
            or (
                inferred[0]
                if inferred
                else None
            )
        )

        if company is None:
            raise CompanyScopeError(
                "Company context is required for business data."
            )

        if company.status != Company.Status.ACTIVE:
            raise CompanyScopeError(
                "Business writes are blocked for a non-active company."
            )

        if (
            current is not None
            and explicit is not None
            and current.pk != explicit.pk
        ):
            raise CompanyScopeError(
                "Explicit company does not match active company context."
            )

        for related_company in inferred:
            if related_company.pk != company.pk:
                raise CompanyScopeError(
                    "Cross-company business relationship is not allowed."
                )

        return company

    def create(self, **kwargs):
        kwargs["company"] = self._company_from_values(
            kwargs
        )

        return super().create(**kwargs)

    def get_or_create(
        self,
        defaults=None,
        **kwargs,
    ):
        kwargs["company"] = self._company_from_values(
            {
                **(defaults or {}),
                **kwargs,
            }
        )

        return super().get_or_create(
            defaults=defaults,
            **kwargs,
        )

    def update_or_create(
        self,
        defaults=None,
        create_defaults=None,
        **kwargs,
    ):
        kwargs["company"] = self._company_from_values(
            {
                **(defaults or {}),
                **(create_defaults or {}),
                **kwargs,
            }
        )

        return super().update_or_create(
            defaults=defaults,
            create_defaults=create_defaults,
            **kwargs,
        )

    def bulk_create(
        self,
        objs,
        **kwargs,
    ):
        company = self._scope_company()

        for obj in objs:
            if obj.company_id is None:
                if company is None:
                    raise CompanyScopeError(
                        "Company context is required for bulk_create."
                    )

                obj.company = company

            obj.validate_company_relations()

        return super().bulk_create(
            objs,
            **kwargs,
        )


class CompanyOwnedModel(models.Model):
    """
    Abstract tenant boundary for company-owned business records.

    ``objects`` is scoped to the active company and excludes archived
    rows. ``all_objects`` is deliberately unscoped and is reserved for
    governance, repair, migrations and controlled tests.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_records",
    )

    is_archived = models.BooleanField(
        default=False,
        db_index=True,
    )

    archived_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    objects = CompanyScopedManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True
        default_manager_name = "objects"
        base_manager_name = "all_objects"

    def _derive_company_from_relations(self):
        for field in self._meta.fields:
            if (
                not field.is_relation
                or field.name == "company"
                or not (
                    field.many_to_one
                    or field.one_to_one
                )
            ):
                continue

            related_model = field.remote_field.model

            if not hasattr(
                related_model,
                "company",
            ):
                continue

            related_id = getattr(
                self,
                field.attname,
                None,
            )

            if related_id is None:
                continue

            if field.is_cached(self):
                related = getattr(
                    self,
                    field.name,
                )

                return related.company

            manager = getattr(
                related_model,
                "all_objects",
                related_model._base_manager,
            )

            company_id = (
                manager
                .filter(pk=related_id)
                .values_list(
                    "company_id",
                    flat=True,
                )
                .first()
            )

            if company_id:
                return Company.objects.get(
                    pk=company_id
                )

        return None

    def validate_company_relations(self):
        if self.company_id is None:
            company = (
                get_current_company()
                or self._derive_company_from_relations()
            )

            if company is None:
                raise CompanyScopeError(
                    "Company context is required for business data."
                )

            self.company = company

        current = get_current_company()

        if (
            current is not None
            and current.pk != self.company_id
        ):
            raise CompanyScopeError(
                "Business record company does not match active company."
            )

        for field in self._meta.fields:
            if (
                not field.is_relation
                or field.name == "company"
                or not (
                    field.many_to_one
                    or field.one_to_one
                )
            ):
                continue

            related_model = field.remote_field.model

            if not hasattr(
                related_model,
                "company",
            ):
                continue

            related_id = getattr(
                self,
                field.attname,
                None,
            )

            if related_id is None:
                continue

            if field.is_cached(self):
                related = getattr(
                    self,
                    field.name,
                )
                related_company_id = (
                    related.company_id
                )

            else:
                manager = getattr(
                    related_model,
                    "all_objects",
                    related_model._base_manager,
                )

                related_company_id = (
                    manager
                    .filter(pk=related_id)
                    .values_list(
                        "company_id",
                        flat=True,
                    )
                    .first()
                )

            if (
                related_company_id is not None
                and related_company_id != self.company_id
            ):
                raise CompanyScopeError(
                    f"{field.name} belongs to another company."
                )

    def save(
        self,
        *args,
        **kwargs,
    ):
        self.validate_company_relations()

        return super().save(
            *args,
            **kwargs,
        )

    def archive(self):
        if self.is_archived:
            return False

        self.is_archived = True
        self.archived_at = timezone.now()

        super().save(
            update_fields=[
                "is_archived",
                "archived_at",
            ]
        )

        return True

    def restore(self):
        if not self.is_archived:
            return False

        self.is_archived = False
        self.archived_at = None

        super().save(
            update_fields=[
                "is_archived",
                "archived_at",
            ]
        )

        return True

    def delete(
        self,
        using=None,
        keep_parents=False,
        *,
        hard=False,
    ):
        if not hard:
            archived = self.archive()

            if archived:
                label = self._meta.label
                return (
                    1,
                    {
                        label: 1,
                    },
                )

            return (
                0,
                {},
            )

        return super().delete(
            using=using,
            keep_parents=keep_parents,
        )

# PIGS-COMPANY-ONBOARDING-STAGE1
from .onboarding_models import (  # noqa: E402,F401
    CompanyInvitationThrottle,
    CompanyOnboardingEvent,
    CompanyOwnerInvitation,
)
