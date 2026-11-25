from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0004_passwordrecoverythrottle_user_phone_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="GoogleIdentity",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("subject", models.CharField(max_length=255, unique=True)),
                ("email_at_link", models.EmailField(max_length=254)),
                ("display_name", models.CharField(blank=True, max_length=255)),
                ("picture_url", models.URLField(blank=True, max_length=500)),
                ("last_login_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="google_identity",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
