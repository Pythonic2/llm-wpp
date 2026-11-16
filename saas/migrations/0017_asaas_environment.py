from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("saas", "0016_staff_password_state")]

    operations = [
        migrations.AddField(
            model_name="billingcustomer", name="asaas_environment",
            field=models.CharField(default="sandbox", max_length=10),
        ),
        migrations.AddField(
            model_name="billingcheckout", name="asaas_environment",
            field=models.CharField(default="sandbox", max_length=10),
        ),
    ]
