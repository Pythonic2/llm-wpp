from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("saas", "0003_message_idempotency_key")]

    operations = [
        migrations.AddField(model_name="conversation", name="lead_temperature", field=models.CharField(choices=[("unknown", "Não analisada"), ("hot", "Quente"), ("warm", "Morna"), ("cold", "Fria")], default="unknown", max_length=12)),
        migrations.AddField(model_name="conversation", name="purchase_intent_score", field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="conversation", name="analysis_summary", field=models.TextField(blank=True)),
        migrations.AddField(model_name="conversation", name="buying_signals", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="conversation", name="objections", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="conversation", name="recommended_action", field=models.CharField(blank=True, max_length=500)),
        migrations.AddField(model_name="conversation", name="analyzed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="conversation", name="opportunity_status", field=models.CharField(choices=[("open", "Em aberto"), ("won", "Ganha"), ("lost", "Perdida")], default="open", max_length=12)),
        migrations.AddField(model_name="conversation", name="next_follow_up_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="conversation", name="follow_up_completed_at", field=models.DateTimeField(blank=True, null=True)),
    ]
