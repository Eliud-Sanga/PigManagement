from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pigs", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pig",
            name="status",
            field=models.CharField(
                choices=[
                    ("AVAILABLE", "Hajachinjwa"),
                    ("SLAUGHTERED", "Amechinjwa"),
                    ("FINISHED", "Nyama imeisha"),
                    ("DECEASED", "Amekufa"),
                ],
                default="AVAILABLE",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="FarmRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_archived", models.BooleanField(db_index=True, default=False)),
                ("archived_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("kind", models.CharField(choices=[
                    ("FEED", "Feed"),
                    ("TREATMENT", "Treatment / medication"),
                    ("WEIGHT", "Weight"),
                    ("BREEDING", "Breeding"),
                    ("MORTALITY", "Mortality"),
                    ("ACTIVITY", "Farm activity"),
                ], max_length=16)),
                ("record_date", models.DateField()),
                ("title", models.CharField(max_length=120)),
                ("quantity", models.DecimalField(
                    blank=True, decimal_places=2, max_digits=12, null=True,
                    validators=[django.core.validators.MinValueValidator(Decimal("0.01"))],
                )),
                ("unit", models.CharField(blank=True, max_length=20)),
                ("cost", models.DecimalField(
                    blank=True, decimal_places=2, max_digits=12, null=True,
                    validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
                )),
                ("notes", models.TextField(blank=True)),
                ("company", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="%(app_label)s_%(class)s_records",
                    to="companies.company",
                )),
                ("pig", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="farm_records", to="pigs.pig",
                )),
            ],
            options={
                "ordering": ["-record_date", "-created_at", "-pk"],
                "indexes": [models.Index(
                    fields=["company", "kind", "record_date"],
                    name="farmrec_company_kind_date_idx",
                )],
            },
            managers=[
                ("objects", models.Manager()),
                ("all_objects", models.Manager()),
            ],
        ),
    ]
