import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("saas", "0012_whatsappnumber_access_token_encrypted_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name="whatsappnumber", name="business_account_name", field=models.CharField(blank=True, max_length=160)),
        migrations.AddField(model_name="whatsappnumber", name="connection_state", field=models.CharField(default="connected", max_length=24)),
        migrations.AddField(model_name="whatsappnumber", name="last_synced_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="whatsappnumber", name="history_sync_requested_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="whatsappnumber", name="contacts_sync_request_id", field=models.CharField(blank=True, max_length=128)),
        migrations.AddField(model_name="whatsappnumber", name="history_sync_request_id", field=models.CharField(blank=True, max_length=128)),
        migrations.AddField(model_name="whatsappnumber", name="pause_ai_on_app_echo", field=models.BooleanField(default=True)),
        migrations.CreateModel(
            name="EmbeddedSignupAttempt",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("standard_migration_consent_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="saas.company")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="CoexistenceWebhookBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fingerprint", models.CharField(max_length=64, unique=True)),
                ("payload", models.JSONField()),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("number", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="saas.whatsappnumber")),
            ],
        ),
        migrations.CreateModel(
            name="WhatsAppBusinessAccount",
            fields=[
                ("business_account_id", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("name", models.CharField(blank=True, max_length=160)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="saas.company")),
            ],
        ),
    ]
