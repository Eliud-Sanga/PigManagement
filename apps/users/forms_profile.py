from django import forms
from django.contrib.auth import get_user_model

from apps.companies.constants import SUPPORTED_LANGUAGE_CHOICES
from apps.companies.models import Company

User = get_user_model()

ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
MAX_IMAGE_BYTES = 3 * 1024 * 1024


def validate_image(upload):
    if upload is None:
        return upload

    if upload.size > MAX_IMAGE_BYTES:
        raise forms.ValidationError(
            "Image must be 3 MB or smaller."
        )

    content_type = getattr(upload, "content_type", "")

    if content_type and content_type not in ALLOWED_IMAGE_TYPES:
        raise forms.ValidationError(
            "Use a JPG, PNG or WEBP image."
        )

    return upload


class PersonalSettingsForm(forms.ModelForm):
    current_password = forms.CharField(
        label="Current password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "data-f2-password-confirm": "1",
            }
        ),
    )

    class Meta:
        model = User
        fields = (
            "username",
            "email",
            "phone",
            "first_name",
            "last_name",
            "preferred_language",
            "theme_preference",
            "profile_photo",
        )
        widgets = {
            "profile_photo": forms.ClearableFileInput(
                attrs={
                    "accept": "image/jpeg,image/png,image/webp",
                }
            ),
        }

    def __init__(self, *args, actor, company=None, **kwargs):
        self.actor = actor
        self.company = company
        super().__init__(*args, **kwargs)

        if company is not None:
            allowed = set(company.allowed_languages or [])
            self.fields["preferred_language"].choices = [
                item
                for item in SUPPORTED_LANGUAGE_CHOICES
                if item[0] in allowed
            ]

        self.fields["theme_preference"].help_text = (
            "System follows the device appearance automatically."
        )

    def clean_username(self):
        username = self.cleaned_data["username"].strip()

        query = User.objects.filter(
            username__iexact=username
        )

        if self.instance.pk:
            query = query.exclude(pk=self.instance.pk)

        if query.exists():
            raise forms.ValidationError(
                "Username already exists."
            )

        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()

        query = User.objects.filter(
            email__iexact=email
        )

        if self.instance.pk:
            query = query.exclude(pk=self.instance.pk)

        if query.exists():
            raise forms.ValidationError(
                "Email already exists."
            )

        return email

    def clean_profile_photo(self):
        return validate_image(
            self.cleaned_data.get("profile_photo")
        )

    def clean_current_password(self):
        password = self.cleaned_data["current_password"]

        if not self.actor.check_password(password):
            raise forms.ValidationError(
                "Current password is incorrect."
            )

        return password


class OwnerCompanySettingsForm(forms.ModelForm):
    current_password = forms.CharField(
        label="Current password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "data-f2-password-confirm": "1",
            }
        ),
    )

    class Meta:
        model = Company
        fields = (
            "name",
            "email",
            "phone",
            "address",
            "logo",
        )
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "logo": forms.ClearableFileInput(
                attrs={
                    "accept": "image/jpeg,image/png,image/webp",
                }
            ),
        }

    def __init__(self, *args, actor, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)

    def clean_logo(self):
        return validate_image(
            self.cleaned_data.get("logo")
        )

    def clean_current_password(self):
        password = self.cleaned_data["current_password"]

        if not self.actor.check_password(password):
            raise forms.ValidationError(
                "Current password is incorrect."
            )

        return password
