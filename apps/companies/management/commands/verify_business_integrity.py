from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.models import F
from django.db.migrations.executor import MigrationExecutor


def _has_company(model):
    try:
        model._meta.get_field("company")
    except Exception:
        return False
    return True


class Command(BaseCommand):
    help = "Read-only migration, constraint and cross-company integrity check."

    def add_arguments(self, parser):
        parser.add_argument("--database", default=DEFAULT_DB_ALIAS)

    def handle(self, *args, **options):
        alias = options["database"]
        if alias not in connections:
            raise CommandError(f"Unknown database alias: {alias}")
        connection = connections[alias]
        connection.ensure_connection()

        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if plan:
            names = ", ".join(f"{migration.app_label}.{migration.name}" for migration, _ in plan)
            raise CommandError(f"Unapplied migrations: {names}")

        connection.check_constraints()
        checked_relations = 0
        violations = []
        for model in apps.get_models():
            if not _has_company(model):
                continue
            manager = model._base_manager.using(alias)
            for field in model._meta.fields:
                related = getattr(field.remote_field, "model", None)
                if related is None or not _has_company(related):
                    continue
                checked_relations += 1
                filters = {f"{field.name}__isnull": False}
                mismatch = manager.filter(**filters).exclude(
                    **{f"{field.name}__company_id": F("company_id")}
                )
                if mismatch.exists():
                    violations.append(f"{model._meta.label}.{field.name}")

            for field in model._meta.many_to_many:
                related = field.remote_field.model
                if not _has_company(related):
                    continue
                checked_relations += 1
                through = field.remote_field.through
                source = field.m2m_field_name()
                target = field.m2m_reverse_field_name()
                mismatch = through._base_manager.using(alias).exclude(
                    **{f"{source}__company_id": F(f"{target}__company_id")}
                )
                if mismatch.exists():
                    violations.append(f"{model._meta.label}.{field.name}")

        if violations:
            raise CommandError(
                "Cross-company relation violations: " + ", ".join(sorted(violations))
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Database integrity verified on {alias}: constraints valid, "
                f"migrations current, {checked_relations} company relations checked."
            )
        )
