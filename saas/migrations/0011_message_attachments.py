import saas.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("saas", "0010_hot_lead_notification_fingerprint")]

    operations = [
        migrations.AddField(
            model_name="message",
            name="attachment",
            field=models.FileField(blank=True, upload_to=saas.models.message_attachment_upload_path),
        ),
        migrations.AddField(model_name="message", name="attachment_error", field=models.TextField(blank=True)),
        migrations.AddField(
            model_name="message", name="attachment_mime_type",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="message", name="attachment_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="message", name="attachment_size",
            field=models.PositiveBigIntegerField(default=0),
        ),
    ]
