"""Create and fully validate an encrypted-at-rest infrastructure backup target."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = "Create and fully read-validate a Home Management database snapshot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            required=True,
            help="Exact snapshot path on a protected backup volume.",
        )

    def handle(self, *args, **options):
        if set(connections) != {"default"}:
            raise CommandError(
                "Multiple database aliases require an explicit backup plan."
            )

        backup = Path(options["output"]).expanduser().resolve()
        if backup == Path("/") or backup.exists() and backup.is_dir():
            raise CommandError("--output must be an exact snapshot file path.")
        if backup.parent.is_symlink():
            raise CommandError("The backup directory cannot be a symlink.")

        backup.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(backup.parent, 0o700)
        temporary = backup.with_name(f".{backup.name}.partial")
        temporary.unlink(missing_ok=True)

        connection = connections["default"]
        config = connection.settings_dict
        engine = config["ENGINE"]
        try:
            if engine.endswith("sqlite3"):
                self._backup_sqlite(config, temporary)
                validation = "sqlite integrity_check"
            elif engine.endswith("postgresql"):
                self._backup_postgresql(config, temporary)
                validation = "full archive read"
            else:
                raise CommandError(f"Unsupported database engine: {engine}")

            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise CommandError("Database snapshot is empty.")
            os.chmod(temporary, 0o600)
            os.replace(temporary, backup)
            digest = hashlib.sha256(backup.read_bytes()).hexdigest()
            manifest = backup.with_suffix(backup.suffix + ".json")
            manifest.write_text(
                json.dumps(
                    {
                        "engine": engine,
                        "file": backup.name,
                        "bytes": backup.stat().st_size,
                        "sha256": digest,
                        "validation": validation,
                    },
                    indent=2,
                )
            )
            os.chmod(manifest, 0o600)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        self.stdout.write(self.style.SUCCESS(f"Validated backup: {backup}"))

    @staticmethod
    def _backup_sqlite(config, target):
        source_path = Path(config["NAME"]).resolve()
        if not source_path.is_file():
            raise CommandError("SQLite database file was not found.")
        with sqlite3.connect(str(source_path)) as source:
            with sqlite3.connect(str(target)) as destination:
                source.backup(destination)
                result = destination.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    raise CommandError(
                        f"SQLite snapshot integrity failed: {result}"
                    )

    @staticmethod
    def _backup_postgresql(config, target):
        pg_dump = shutil.which("pg_dump")
        pg_restore = shutil.which("pg_restore")
        if not pg_dump or not pg_restore:
            raise CommandError("Matching pg_dump and pg_restore are required.")

        environment = os.environ.copy()
        environment["PGPASSWORD"] = config.get("PASSWORD") or ""
        db_options = config.get("OPTIONS") or {}
        for option, variable in (
            ("sslmode", "PGSSLMODE"),
            ("sslrootcert", "PGSSLROOTCERT"),
        ):
            if db_options.get(option):
                environment[variable] = str(db_options[option])

        command = [
            pg_dump,
            "--format=custom",
            "--no-owner",
            "--file",
            str(target),
        ]
        for key, flag in (
            ("HOST", "--host"),
            ("PORT", "--port"),
            ("USER", "--username"),
        ):
            if config.get(key):
                command.extend([flag, str(config[key])])
        command.extend(["--", str(config["NAME"])])

        try:
            subprocess.run(
                command,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            subprocess.run(
                [pg_restore, "--list", str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            subprocess.run(
                [pg_restore, "--file", os.devnull, str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as error:
            raise CommandError(
                "PostgreSQL snapshot validation failed "
                f"(exit {error.returncode})."
            ) from error
