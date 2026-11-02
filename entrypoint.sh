#!/bin/sh
set -e
.venv/bin/python manage.py ensure_db_schema
.venv/bin/python manage.py collectstatic --noinput
exec .venv/bin/gunicorn gesttech.wsgi:application \
  --bind 0.0.0.0:8123 \
  --workers "${GUNICORN_WORKERS:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-180}" \
  --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
  --keep-alive "${GUNICORN_KEEP_ALIVE:-5}"
