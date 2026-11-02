#!/bin/sh
set -e
.venv/bin/python manage.py ensure_db_schema
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
exec .venv/bin/gunicorn gesttech.wsgi:application --bind 0.0.0.0:8123 --workers 2
