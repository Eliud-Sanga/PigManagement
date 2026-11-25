from django.apps import AppConfig


class CompaniesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.companies"
    verbose_name = "Companies"

    def ready(self):
        # Register tenancy integrity signals.
        from . import signals  # noqa: F401