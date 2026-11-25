#!/usr/bin/env bash
set -euo pipefail
python manage.py migrate --check
exec gunicorn pig_management.wsgi:application --bind "0.0.0.0:${PORT:-10000}" --workers "${WEB_CONCURRENCY:-2}" --threads 2 --timeout 60
