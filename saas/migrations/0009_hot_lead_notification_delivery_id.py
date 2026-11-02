from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("saas", "0008_mediaasset_allow_ai_send_default")]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="hot_lead_notification_message_id",
            field=models.CharField(blank=True, max_length=128),
        ),
    ]
