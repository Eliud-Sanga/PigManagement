from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q

from apps.companies.constants import (
    DELEGABLE_COMPANY_PERMISSION_CODENAMES,
    SUPPORTED_LANGUAGE_CHOICES,
)
from apps.companies.models import Company, CompanyMembership, CompanyRole


User = get_user_model()


def delegable_permissions():
    return (
        Permission.objects
        .filter(
            Q(content_type__app_label__in=("pigs", "transport"))
            |
            Q(
                content_type__app_label="companies",
                codename__in=DELEGABLE_COMPANY_PERMISSION_CODENAMES,
            )
        )
        .select_related("content_type")
        .order_by(
            "content_type__app_label",
            "content_type__model",
            "codename",
        )
    )


def permission_code(permission):
    return (
        f"{permission.content_type.app_label}."
        f"{permission.codename}"
    )


def permission_choices():
    return [
        (
            permission_code(permission),
            f"{permission.name} ({permission_code(permission)})",
        )
        for permission in delegable_permissions()
    ]


class AdminPasswordMixin(forms.Form):
    admin_password = forms.CharField(
        label="Confirm current administrator password",
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password"}
        ),
        strip=False,
    )


class CompanyCreateForm(AdminPasswordMixin):
    business_type = forms.ChoiceField(
        choices=Company.BusinessType.choices,
        initial=Company.BusinessType.PIG,
        required=False,
    )
    transport_cargo_enabled = forms.BooleanField(required=False, initial=False)
    transport_passenger_enabled = forms.BooleanField(required=False, initial=False)
    name = forms.CharField(max_length=160)
    code = forms.SlugField(
        max_length=50,
        help_text="Stable company code. It cannot be edited after creation.",
    )
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=40, required=False)
    address = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    timezone = forms.CharField(
        max_length=64,
        initial="Africa/Dar_es_Salaam",
    )
    allowed_languages = forms.MultipleChoiceField(
        choices=SUPPORTED_LANGUAGE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )
    default_language = forms.ChoiceField(
        choices=SUPPORTED_LANGUAGE_CHOICES,
    )
    owner_email = forms.EmailField(
        help_text=(
            "If this email already exists, the same user will become "
            "Owner of this additional company."
        ),
    )
    owner_username = forms.CharField(
        max_length=150,
        required=False,
        help_text="Required only for a brand-new Owner identity.",
    )
    owner_first_name = forms.CharField(max_length=150, required=False)
    owner_last_name = forms.CharField(max_length=150, required=False)
    temporary_password1 = forms.CharField(
        label="Temporary password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password"}
        ),
        help_text=(
            "Required for a new Owner or an identity with no usable "
            "password. First login will force a password change."
        ),
    )
    temporary_password2 = forms.CharField(
        label="Confirm temporary password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password"}
        ),
    )

    def clean_code(self):
        code = self.cleaned_data["code"].strip().lower()
        if Company.objects.filter(code__iexact=code).exists():
            raise forms.ValidationError("Company code already exists.")
        return code

    def clean_timezone(self):
        value = self.cleaned_data["timezone"].strip()
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise forms.ValidationError("Unknown timezone.") from exc
        return value

    def clean_owner_email(self):
        return self.cleaned_data["owner_email"].strip().lower()

    def clean(self):
        cleaned = super().clean()

        business_type = cleaned.get("business_type") or Company.BusinessType.PIG
        cleaned["business_type"] = business_type
        if business_type == Company.BusinessType.TRANSPORT and not (
            cleaned.get("transport_cargo_enabled")
            or cleaned.get("transport_passenger_enabled")
        ):
            self.add_error(
                "transport_cargo_enabled",
                "Select Cargo, Passengers, or both Transport services.",
            )
        if business_type != Company.BusinessType.TRANSPORT:
            cleaned["transport_cargo_enabled"] = False
            cleaned["transport_passenger_enabled"] = False

        allowed = set(cleaned.get("allowed_languages") or ())
        default_language = cleaned.get("default_language")
        if default_language and default_language not in allowed:
            self.add_error(
                "default_language",
                "Default language must be one of the enabled languages.",
            )

        email = cleaned.get("owner_email")
        username = (cleaned.get("owner_username") or "").strip()
        password1 = cleaned.get("temporary_password1")
        password2 = cleaned.get("temporary_password2")

        owner = (
            User.objects.filter(email__iexact=email).first()
            if email
            else None
        )
        self.existing_owner = owner

        if owner is not None and not owner.is_active:
            self.add_error(
                "owner_email",
                "This existing account is inactive. Resolve its account status "
                "before adding it as a company Owner.",
            )

        if owner is None and not username:
            self.add_error(
                "owner_username",
                "Username is required for a new Owner.",
            )

        if (
            owner is None
            and username
            and User.objects.filter(username__iexact=username).exists()
        ):
            self.add_error("owner_username", "Username already exists.")

        password_required = (
            owner is None
            or (
                owner is not None
                and not owner.has_usable_password()
            )
        )

        if (
            owner is not None
            and owner.has_usable_password()
            and (password1 or password2)
        ):
            self.add_error(
                "temporary_password1",
                (
                    "Existing Owner already has a usable global password. "
                    "Do not reset it while adding another company; use the "
                    "dedicated secure password-reset workflow instead."
                ),
            )

        if password_required and not password1:
            self.add_error(
                "temporary_password1",
                "Temporary password is required.",
            )

        if password1 or password2:
            if password1 != password2:
                self.add_error(
                    "temporary_password2",
                    "Passwords do not match.",
                )
            elif password1:
                candidate = (
                    owner
                    or User(
                        username=username,
                        email=email or "",
                        first_name=(cleaned.get("owner_first_name") or ""),
                        last_name=(cleaned.get("owner_last_name") or ""),
                    )
                )
                try:
                    validate_password(password1, user=candidate)
                except DjangoValidationError as exc:
                    self.add_error("temporary_password1", exc)

        return cleaned


class CompanyEditForm(AdminPasswordMixin, forms.ModelForm):
    class Meta:
        model = Company
        fields = ("name", "email", "phone", "address", "timezone")
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_timezone(self):
        value = self.cleaned_data["timezone"].strip()
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise forms.ValidationError("Unknown timezone.") from exc
        return value


class CompanyLanguageForm(AdminPasswordMixin):
    allowed_languages = forms.MultipleChoiceField(
        choices=SUPPORTED_LANGUAGE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )
    default_language = forms.ChoiceField(
        choices=SUPPORTED_LANGUAGE_CHOICES,
    )

    def __init__(self, *args, company, **kwargs):
        self.company = company
        initial = kwargs.setdefault("initial", {})
        initial.setdefault(
            "allowed_languages",
            list(company.allowed_languages or []),
        )
        initial.setdefault(
            "default_language",
            company.default_language,
        )
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        allowed = set(cleaned.get("allowed_languages") or ())
        default_language = cleaned.get("default_language")
        if default_language and default_language not in allowed:
            self.add_error(
                "default_language",
                "Default language must be enabled for the company.",
            )
        return cleaned


class CompanyMemberRequestForm(AdminPasswordMixin):
    username = forms.CharField(max_length=150, required=False)
    email = forms.EmailField()
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    role = forms.ModelChoiceField(
        queryset=CompanyRole.objects.none(),
    )
    requested_language = forms.ChoiceField(choices=())

    def __init__(self, *args, company, **kwargs):
        self.company = company
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = (
            company.roles
            .filter(is_active=True)
            .order_by("is_reserved", "name")
        )
        allowed = set(company.allowed_languages or [])
        self.fields["requested_language"].choices = [
            item
            for item in SUPPORTED_LANGUAGE_CHOICES
            if item[0] in allowed
        ]

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")
        username = (cleaned.get("username") or "").strip()

        if not email:
            return cleaned

        existing = User.objects.filter(email__iexact=email).first()

        if existing is None:
            if not username:
                self.add_error(
                    "username",
                    "Username is required for a brand-new identity.",
                )
            elif User.objects.filter(username__iexact=username).exists():
                self.add_error("username", "Username already exists.")
        elif username and existing.username != username:
            self.add_error(
                "username",
                (
                    "This email already belongs to "
                    f"username '{existing.username}'."
                ),
            )

        if (
            existing is not None
            and CompanyMembership.objects.filter(
                company=self.company,
                user=existing,
            ).exists()
        ):
            self.add_error(
                "email",
                "This user already has a membership record in this company.",
            )

        return cleaned


class MembershipApprovalForm(AdminPasswordMixin):
    approved_language = forms.ChoiceField(choices=())

    def __init__(self, *args, membership, **kwargs):
        self.membership = membership
        initial = kwargs.setdefault("initial", {})
        initial.setdefault(
            "approved_language",
            membership.requested_language,
        )
        super().__init__(*args, **kwargs)

        allowed = set(
            membership.company.allowed_languages or []
        )

        self.fields["approved_language"].choices = [
            item
            for item in SUPPORTED_LANGUAGE_CHOICES
            if item[0] in allowed
        ]

        if not membership.user.has_usable_password():
            self.fields["approved_language"].help_text = (
                "New identity: approval sends a secure one-time "
                "activation link so the user chooses their own password."
            )


class MembershipEditForm(AdminPasswordMixin):
    role = forms.ModelChoiceField(
        queryset=CompanyRole.objects.none(),
    )
    approved_language = forms.ChoiceField(choices=())

    def __init__(self, *args, membership, **kwargs):
        self.membership = membership
        initial = kwargs.setdefault("initial", {})
        initial.setdefault("role", membership.role_id)
        initial.setdefault(
            "approved_language",
            membership.approved_language
            or membership.requested_language,
        )
        super().__init__(*args, **kwargs)

        self.fields["role"].queryset = (
            membership.company.roles
            .filter(is_active=True)
            .order_by("is_reserved", "name")
        )

        allowed = set(membership.company.allowed_languages or [])
        self.fields["approved_language"].choices = [
            item
            for item in SUPPORTED_LANGUAGE_CHOICES
            if item[0] in allowed
        ]


class RoleCreateForm(AdminPasswordMixin):
    code = forms.CharField(max_length=50)
    name = forms.CharField(max_length=100)
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    permissions = forms.MultipleChoiceField(
        choices=(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, company, **kwargs):
        self.company = company
        super().__init__(*args, **kwargs)
        self.fields["permissions"].choices = permission_choices()

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        if self.company.roles.filter(code__iexact=code).exists():
            raise forms.ValidationError(
                "Role code already exists in this company."
            )
        return code

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self.company.roles.filter(name__iexact=name).exists():
            raise forms.ValidationError(
                "Role name already exists in this company."
            )
        return name


class RolePermissionsForm(AdminPasswordMixin):
    permissions = forms.MultipleChoiceField(
        choices=(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, role, **kwargs):
        self.role = role
        initial = kwargs.setdefault("initial", {})
        initial.setdefault(
            "permissions",
            [
                permission_code(permission)
                for permission
                in role.permissions.select_related("content_type").all()
            ],
        )
        super().__init__(*args, **kwargs)
        self.fields["permissions"].choices = permission_choices()


class MembershipOverridesForm(AdminPasswordMixin):
    granted_permissions = forms.MultipleChoiceField(
        label="Extra grants",
        choices=(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    denied_permissions = forms.MultipleChoiceField(
        label="Explicit denials",
        choices=(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, membership, **kwargs):
        self.membership = membership
        initial = kwargs.setdefault("initial", {})
        initial.setdefault(
            "granted_permissions",
            [
                permission_code(permission)
                for permission
                in membership.granted_permissions
                .select_related("content_type")
                .all()
            ],
        )
        initial.setdefault(
            "denied_permissions",
            [
                permission_code(permission)
                for permission
                in membership.denied_permissions
                .select_related("content_type")
                .all()
            ],
        )
        super().__init__(*args, **kwargs)
        choices = permission_choices()
        self.fields["granted_permissions"].choices = choices
        self.fields["denied_permissions"].choices = choices

    def clean(self):
        cleaned = super().clean()
        grants = set(cleaned.get("granted_permissions") or ())
        denials = set(cleaned.get("denied_permissions") or ())
        overlap = grants & denials
        if overlap:
            raise forms.ValidationError(
                "The same permission cannot be both granted and denied: "
                + ", ".join(sorted(overlap))
            )
        return cleaned
