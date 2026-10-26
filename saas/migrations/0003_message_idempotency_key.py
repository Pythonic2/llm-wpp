from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("saas", "0002_conversation_ai_paused_conversation_ai_paused_at_and_more")]

    operations = [
        migrations.AddField(
            model_name="message",
            name="idempotency_key",
            field=models.CharField(blank=True, max_length=128, null=True, unique=True),
        ),
    ]
