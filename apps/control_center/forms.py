from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import SecuritySettings


User = get_user_model()


class SecuritySettingsForm(forms.ModelForm):
    admin_password = forms.CharField(
        label="Confirm administrator password",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete":
                    "current-password",
            }
        ),
        strip=False,
    )

    class Meta:
        model = SecuritySettings
        fields = (
            "max_failed_login_attempts",
            "failed_attempt_window_minutes",
            "login_lock_minutes",
            "device_cookie_days",
            "idle_timeout_minutes",
            "absolute_session_timeout_minutes",
            "maintenance_enabled",
            "maintenance_starts_at",
            "maintenance_ends_at",
            "maintenance_message",
        )
        labels = {
            "max_failed_login_attempts":
                "Maximum failed login attempts",
            "failed_attempt_window_minutes":
                "Failed-attempt window (minutes)",
            "login_lock_minutes":
                "Login lock duration (minutes)",
            "device_cookie_days":
                "Device trust cookie (days)",
            "idle_timeout_minutes":
                "Idle logout timeout (minutes)",
            "absolute_session_timeout_minutes":
                "Maximum session lifetime (minutes)",
            "maintenance_enabled":
                "Enable maintenance mode",
            "maintenance_starts_at":
                "Maintenance starts",
            "maintenance_ends_at":
                "Maintenance ends",
            "maintenance_message":
                "Maintenance message",
        }
        widgets = {
            "maintenance_starts_at":
                forms.DateTimeInput(
                    attrs={
                        "type":
                            "datetime-local",
                    }
                ),
            "maintenance_ends_at":
                forms.DateTimeInput(
                    attrs={
                        "type":
                            "datetime-local",
                    }
                ),
            "maintenance_message":
                forms.Textarea(
                    attrs={
                        "rows": 3,
                    }
                ),
        }


class SecureUserIdentityForm(forms.ModelForm):
    account_revision = forms.CharField(required=False, label='', widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            from .account_state import revision
            self.initial['account_revision'] = revision(self.instance)


    admin_password = forms.CharField(
        label="Confirm administrator password",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete":
                    "current-password",
            }
        ),
        strip=False,
    )

    class Meta:
        model = User
        fields = (
            "username",
            "email",
            "phone",
            "first_name",
            "last_name",
            "is_active",
        )

    def clean_username(self):
        username = (
            self.cleaned_data["username"]
            .strip()
        )

        queryset = User.objects.filter(
            username__iexact=username
        )

        if self.instance.pk:
            queryset = queryset.exclude(
                pk=self.instance.pk
            )

        if queryset.exists():
            raise forms.ValidationError(
                "Username already exists."
            )

        return username

    def clean_email(self):
        email = (
            self.cleaned_data["email"]
            .strip()
            .lower()
        )

        queryset = User.objects.filter(
            email__iexact=email
        )

        if self.instance.pk:
            queryset = queryset.exclude(
                pk=self.instance.pk
            )

        if queryset.exists():
            raise forms.ValidationError(
                "Email already exists."
            )

        return email


class SecurePasswordChangeForm(forms.Form):
    current_password = forms.CharField(
        label="Current password",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete":
                    "current-password",
            }
        ),
        strip=False,
    )

    new_password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete":
                    "new-password",
            }
        ),
        strip=False,
    )

    new_password2 = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete":
                    "new-password",
            }
        ),
        strip=False,
    )

    def __init__(
        self,
        *args,
        actor,
        target,
        **kwargs,
    ):
        self.actor = actor
        self.target = target

        super().__init__(
            *args,
            **kwargs,
        )

        if actor.pk != target.pk:
            self.fields[
                "current_password"
            ].label = (
                "Administrator password"
            )

    def clean_current_password(self):
        password = self.cleaned_data[
            "current_password"
        ]

        # Self-service requires the user's own old/current password.
        # Admin reset of another user verifies the administrator's own
        # password; administrators should never know a user's password.
        verifier = (
            self.target
            if self.actor.pk == self.target.pk
            else self.actor
        )

        if not verifier.check_password(
            password
        ):
            raise forms.ValidationError(
                "Current password is incorrect."
            )

        return password

    def clean(self):
        cleaned = super().clean()

        password1 = cleaned.get(
            "new_password1"
        )
        password2 = cleaned.get(
            "new_password2"
        )

        if (
            password1
            and password2
            and password1 != password2
        ):
            self.add_error(
                "new_password2",
                "Passwords do not match.",
            )
            return cleaned

        if password1:
            try:
                validate_password(
                    password1,
                    user=self.target,
                )
            except DjangoValidationError as exc:
                self.add_error(
                    "new_password1",
                    exc,
                )

        return cleaned
