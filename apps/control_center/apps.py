from django.apps import AppConfig


class ControlCenterConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.control_center"
    verbose_name = "PIGS Control Center"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in
        from .middleware import initialize_session_policy

        user_logged_in.connect(
            initialize_session_policy,
            dispatch_uid="home.session_policy.initialize",
        )
