from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("saas", "0005_company_ai_instructions")]

    operations = [
        migrations.AddField(
            model_name="company",
            name="hot_lead_notifications_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="company",
            name="hot_lead_notification_phone",
            field=models.CharField(
                blank=True,
                help_text="WhatsApp que recebe alertas de leads quentes, com DDI e DDD",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="hot_lead_notified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
