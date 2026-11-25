from django import forms
from django.contrib.auth import get_user_model

from .constants import SUPPORTED_LANGUAGE_CHOICES
from .models import CompanyMembership, CompanyRole


User = get_user_model()


class CompanyUserRequestForm(forms.Form):
    username = forms.CharField(max_length=150, required=False)
    email = forms.EmailField()
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    phone = forms.CharField(
        max_length=32,
        required=False,
        label="Phone",
        help_text=(
            "Required for a new user identity and secure account recovery."
        ),
    )

    role = forms.ModelChoiceField(
        queryset=CompanyRole.objects.none(),
    )

    requested_language = forms.ChoiceField(
        choices=(),
    )

    current_password = forms.CharField(
        label="Confirm your password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password"}
        ),
    )

    def __init__(self, *args, company, **kwargs):
        self.company = company
        super().__init__(*args, **kwargs)

        self.fields["role"].queryset = (
            company.roles
            .filter(is_active=True)
            .exclude(code="OWNER")
            .order_by("name")
        )

        allowed = set(
            company.allowed_languages or []
        )

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

        existing = (
            User.objects
            .filter(email__iexact=email)
            .first()
        )

        if existing is None:
            if not username:
                self.add_error(
                    "username",
                    "Username is required for a new identity.",
                )
            elif User.objects.filter(
                username__iexact=username
            ).exists():
                self.add_error(
                    "username",
                    "Username already exists.",
                )
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
            and CompanyMembership.objects
            .filter(
                company=self.company,
                user=existing,
            )
            .exists()
        ):
            self.add_error(
                "email",
                (
                    "This user already has a membership "
                    "record in this company."
                ),
            )

        # PIGS-SYSTEM-LOGIC-CLOSURE-V3: recovery identity rules
        self.existing_user = existing

        if existing is None:
            first_name = (cleaned.get("first_name") or "").strip()
            last_name = (cleaned.get("last_name") or "").strip()
            phone = (cleaned.get("phone") or "").strip()

            cleaned["first_name"] = first_name
            cleaned["last_name"] = last_name
            cleaned["phone"] = phone

            if not first_name:
                self.add_error(
                    "first_name",
                    "First name is required for a new user identity.",
                )
            if not last_name:
                self.add_error(
                    "last_name",
                    "Last name is required for a new user identity.",
                )
            if not phone:
                self.add_error(
                    "phone",
                    "Phone is required for secure account recovery.",
                )
        else:
            # Existing global identity profile/recovery data is not rewritten.
            cleaned["first_name"] = existing.first_name
            cleaned["last_name"] = existing.last_name
            cleaned["phone"] = existing.phone

        return cleaned
