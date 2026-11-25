from django.db import migrations, models


def allow_transport_permissions(apps, schema_editor):
    # No data rewrite is required. This migration updates only the editable
    # permission filters so Transport permissions can be assigned to roles.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0010_company_business_type_transport_options"),
        ("transport", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="companyrole",
            name="permissions",
            field=models.ManyToManyField(
                blank=True,
                limit_choices_to={"content_type__app_label__in": ["pigs", "transport", "companies"]},
                related_name="company_roles",
                to="auth.permission",
            ),
        ),
        migrations.AlterField(
            model_name="companymembership",
            name="granted_permissions",
            field=models.ManyToManyField(
                blank=True,
                limit_choices_to={"content_type__app_label__in": ["pigs", "transport", "companies"]},
                related_name="company_membership_grants",
                to="auth.permission",
            ),
        ),
        migrations.AlterField(
            model_name="companymembership",
            name="denied_permissions",
            field=models.ManyToManyField(
                blank=True,
                limit_choices_to={"content_type__app_label__in": ["pigs", "transport", "companies"]},
                related_name="company_membership_denials",
                to="auth.permission",
            ),
        ),
    ]
