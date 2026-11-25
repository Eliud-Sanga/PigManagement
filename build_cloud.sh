#!/usr/bin/env bash
set -euo pipefail
python -m pip install -r requirements.txt
python manage.py check --deploy --fail-level ERROR
python manage.py collectstatic --noinput
