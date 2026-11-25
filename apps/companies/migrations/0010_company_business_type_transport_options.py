from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0009_secure_user_activation_v1"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="business_type",
            field=models.CharField(
                choices=[("pig", "Pig Business"), ("transport", "Transport")],
                db_index=True,
                default="pig",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="companyownerinvitation",
            name="business_type",
            field=models.CharField(default="pig", max_length=30),
        ),
        migrations.AddField(
            model_name="companyownerinvitation",
            name="transport_cargo_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="companyownerinvitation",
            name="transport_passenger_enabled",
            field=models.BooleanField(default=False),
        ),
    ]
