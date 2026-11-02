from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Cria o schema PostgreSQL configurado antes de executar as migrações."

    def handle(self, *args, **options):
        schema = settings.DB_SCHEMA
        quoted_schema = connection.ops.quote_name(schema)
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {quoted_schema}")
        self.stdout.write(self.style.SUCCESS(f"Schema PostgreSQL '{schema}' pronto."))
