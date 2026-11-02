from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("saas", "0009_hot_lead_notification_delivery_id")]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="hot_lead_notification_fingerprint",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
