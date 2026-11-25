import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms

from .constants import SUPPORTED_LANGUAGE_CHOICES
from .models import Company
from .onboarding_models import CompanyOwnerInvitation


BLOCKED_STATUSES = (
    CompanyOwnerInvitation.Status.SUBMITTED,
    CompanyOwnerInvitation.Status.APPROVED,
    CompanyOwnerInvitation.Status.COMPLETED,
)


def _safe_text(value, *, label, required=False):
    value = (value or "").strip()
    if required and not value:
        raise forms.ValidationError(f"{label} is required.")
    if "<" in value or ">" in value or "\x00" in value:
        raise forms.ValidationError(f"{label} contains unsupported characters.")
    for char in value:
        if ord(char) < 32 and char not in "\n\t\r":
            raise forms.ValidationError(
                f"{label} contains unsupported control characters."
            )
    return value


class OwnerInvitationCreateForm(forms.Form):
    email = forms.EmailField(
        label="Prospective Owner email",
        help_text="The secure registration link will be bound to this email.",
    )
    expires_hours = forms.ChoiceField(
        label="Invitation validity",
        choices=(
            ("24", "24 hours"),
            ("48", "48 hours"),
            ("72", "72 hours"),
            ("168", "7 days"),
        ),
        initial="72",
    )
    admin_password = forms.CharField(
        label="Confirm current administrator password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class CompanyRegistrationRequestForm(forms.Form):
    business_type = forms.ChoiceField(
        choices=(("pig", "Pig Business"), ("transport", "Transport")),
        required=False,
        widget=forms.HiddenInput,
    )
    transport_cargo_enabled = forms.BooleanField(required=False, initial=False, widget=forms.HiddenInput)
    transport_passenger_enabled = forms.BooleanField(required=False, initial=False, widget=forms.HiddenInput)
    company_name = forms.CharField(max_length=160)
    company_code = forms.SlugField(
        max_length=50,
        help_text="Short permanent company code, e.g. mwanzo-pork.",
    )
    company_email = forms.EmailField(required=False)
    company_phone = forms.CharField(max_length=40, required=False)
    company_address = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    company_timezone = forms.CharField(
        max_length=64,
        initial="Africa/Dar_es_Salaam",
    )
    allowed_languages = forms.MultipleChoiceField(
        choices=SUPPORTED_LANGUAGE_CHOICES,
        initial=("sw", "en"),
        widget=forms.CheckboxSelectMultiple,
    )
    default_language = forms.ChoiceField(
        choices=SUPPORTED_LANGUAGE_CHOICES,
        initial="sw",
    )

    def __init__(self, *args, invitation, **kwargs):
        self.invitation = invitation
        initial = kwargs.setdefault("initial", {})
        initial.setdefault("business_type", invitation.business_type or "pig")
        initial.setdefault("transport_cargo_enabled", invitation.transport_cargo_enabled)
        initial.setdefault("transport_passenger_enabled", invitation.transport_passenger_enabled)
        super().__init__(*args, **kwargs)

    def clean_company_name(self):
        return _safe_text(
            self.cleaned_data["company_name"],
            label="Company name",
            required=True,
        )

    def clean_company_code(self):
        code = self.cleaned_data["company_code"].strip().lower()
        if Company.objects.filter(code__iexact=code).exists():
            raise forms.ValidationError("That company code is already in use.")

        collision = (
            CompanyOwnerInvitation.objects
            .filter(
                company_code__iexact=code,
                status__in=BLOCKED_STATUSES,
            )
            .exclude(pk=self.invitation.pk)
            .exists()
        )
        if collision:
            raise forms.ValidationError(
                "That company code is already reserved by another request."
            )
        return code

    def clean_company_phone(self):
        phone = _safe_text(
            self.cleaned_data.get("company_phone"),
            label="Company phone",
        )
        if phone and not re.fullmatch(r"[0-9+() -]{5,40}", phone):
            raise forms.ValidationError(
                "Use only normal phone-number characters."
            )
        return phone

    def clean_company_address(self):
        return _safe_text(
            self.cleaned_data.get("company_address"),
            label="Company address",
        )

    def clean_company_timezone(self):
        value = self.cleaned_data["company_timezone"].strip()
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise forms.ValidationError("Unknown timezone.") from exc
        return value

    def clean(self):
        cleaned = super().clean()
        business_type = cleaned.get("business_type") or self.invitation.business_type or "pig"
        cleaned["business_type"] = business_type
        if business_type == "transport" and not (
            cleaned.get("transport_cargo_enabled")
            or cleaned.get("transport_passenger_enabled")
        ):
            self.add_error(
                "transport_cargo_enabled",
                "Select Cargo, Passenger, or both Transport services.",
            )
        if business_type != "transport":
            cleaned["transport_cargo_enabled"] = False
            cleaned["transport_passenger_enabled"] = False
        allowed = list(cleaned.get("allowed_languages") or [])
        default_language = cleaned.get("default_language")
        if not allowed:
            self.add_error("allowed_languages", "At least one language is required.")
        if default_language and default_language not in set(allowed):
            self.add_error(
                "default_language",
                "Default language must be enabled.",
            )
        return cleaned

class RegistrationApprovalForm(forms.Form):
    continuation_hours = forms.ChoiceField(
        label="Owner setup link validity",
        choices=(
            ("24", "24 hours"),
            ("48", "48 hours"),
            ("72", "72 hours"),
            ("168", "7 days"),
        ),
        initial="72",
    )
    admin_password = forms.CharField(
        label="Confirm current administrator password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password"}
        ),
    )


class RegistrationRejectionForm(forms.Form):
    decision_reason = forms.CharField(
        label="Reason / review note",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    admin_password = forms.CharField(
        label="Confirm current administrator password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password"}
        ),
    )

    def clean_decision_reason(self):
        return _safe_text(
            self.cleaned_data["decision_reason"],
            label="Decision reason",
            required=True,
        )

class OwnerCompletionForm(forms.Form):
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    phone = forms.CharField(max_length=32)
    current_password = forms.CharField(
        label="Current account password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password"}
        ),
    )
    new_password1 = forms.CharField(
        label="Create password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password"}
        ),
    )
    new_password2 = forms.CharField(
        label="Confirm password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password"}
        ),
    )

    def __init__(self, *args, invitation, **kwargs):
        from django.contrib.auth import get_user_model

        self.invitation = invitation
        User = get_user_model()
        self.existing_user = User.objects.filter(
            email__iexact=invitation.email
        ).first()

        initial = kwargs.setdefault("initial", {})
        if self.existing_user is not None:
            initial.update(
                {
                    "username": self.existing_user.username,
                    "first_name": self.existing_user.first_name,
                    "last_name": self.existing_user.last_name,
                    "phone": self.existing_user.phone,
                }
            )

        super().__init__(*args, **kwargs)

        if self.existing_user is not None:
            for field_name in (
                "username",
                "first_name",
                "last_name",
                "phone",
            ):
                self.fields[field_name].disabled = True

            if self.existing_user.has_usable_password():
                self.fields["current_password"].required = True
                self.fields["new_password1"].disabled = True
                self.fields["new_password2"].disabled = True
            else:
                self.fields["current_password"].disabled = True
                self.fields["new_password1"].required = True
                self.fields["new_password2"].required = True
        else:
            self.fields["current_password"].disabled = True
            self.fields["new_password1"].required = True
            self.fields["new_password2"].required = True

    def clean_username(self):
        from django.contrib.auth import get_user_model

        value = (self.cleaned_data["username"] or "").strip()
        User = get_user_model()
        if self.existing_user is None:
            if User.objects.filter(username__iexact=value).exists():
                raise forms.ValidationError("Username already exists.")
        return value

    def clean_first_name(self):
        return _safe_text(
            self.cleaned_data["first_name"],
            label="First name",
            required=True,
        )

    def clean_last_name(self):
        return _safe_text(
            self.cleaned_data["last_name"],
            label="Last name",
            required=True,
        )

    def clean_phone(self):
        phone = _safe_text(
            self.cleaned_data["phone"],
            label="Phone",
            required=True,
        )
        if not re.fullmatch(r"[0-9+() -]{5,32}", phone):
            raise forms.ValidationError(
                "Use only normal phone-number characters."
            )
        return phone

    def clean_current_password(self):
        value = self.cleaned_data.get("current_password") or ""
        if (
            self.existing_user is not None
            and self.existing_user.has_usable_password()
            and not self.existing_user.check_password(value)
        ):
            raise forms.ValidationError(
                "Current account password is incorrect."
            )
        return value

    def clean(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        cleaned = super().clean()

        if (
            self.existing_user is not None
            and self.existing_user.is_platform_admin
        ):
            raise forms.ValidationError(
                "Platform Administrator identity cannot become "
                "a Company Owner."
            )

        needs_new_password = (
            self.existing_user is None
            or not self.existing_user.has_usable_password()
        )

        password1 = cleaned.get("new_password1")
        password2 = cleaned.get("new_password2")

        if needs_new_password:
            if password1 != password2:
                self.add_error(
                    "new_password2",
                    "Passwords do not match.",
                )
                return cleaned

            if password1:
                User = get_user_model()
                if self.existing_user is not None:
                    candidate = self.existing_user
                else:
                    candidate = User(
                        username=cleaned.get("username", ""),
                        email=self.invitation.email,
                        first_name=cleaned.get("first_name", ""),
                        last_name=cleaned.get("last_name", ""),
                    )
                try:
                    validate_password(
                        password1,
                        user=candidate,
                    )
                except DjangoValidationError as exc:
                    self.add_error("new_password1", exc)

        return cleaned
