from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("saas", "0019_warm_follow_up")]

    operations = [
        migrations.AddField(
            model_name="company",
            name="trial_expires_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="teste até"),
        ),
        migrations.AddField(
            model_name="company",
            name="archived_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="arquivada em"),
        ),
    ]
