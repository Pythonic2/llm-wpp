from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("saas", "0007_media_library_and_audio")]

    operations = [
        migrations.AlterField(
            model_name="mediaasset",
            name="allow_ai_send",
            field=models.BooleanField(default=True),
        ),
    ]
