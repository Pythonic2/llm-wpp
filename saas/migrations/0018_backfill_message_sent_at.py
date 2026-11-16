"""Use the original WhatsApp timestamp for messages received before the code change."""

from datetime import datetime, timezone

from django.db import migrations


def backfill_sent_at(apps, schema_editor):
    Message = apps.get_model("saas", "Message")
    database = schema_editor.connection.alias
    for message in Message.objects.using(database).filter(sent_at__isnull=True).only(
        "pk", "raw_payload"
    ).iterator(chunk_size=500):
        payload = message.raw_payload
        value = payload.get("timestamp") if isinstance(payload, dict) else None
        try:
            timestamp = datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        if 2020 <= timestamp.year <= 2100:
            Message.objects.using(database).filter(pk=message.pk, sent_at__isnull=True).update(sent_at=timestamp)


class Migration(migrations.Migration):
    dependencies = [("saas", "0017_asaas_environment")]
    operations = [migrations.RunPython(backfill_sent_at, migrations.RunPython.noop)]
