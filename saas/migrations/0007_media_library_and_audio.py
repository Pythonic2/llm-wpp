import django.db.models.deletion
import saas.models
import uuid
from django.db import migrations, models
import pgvector.django.vector


def create_default_categories(apps, schema_editor):
    Company = apps.get_model("saas", "Company")
    MediaCategory = apps.get_model("saas", "MediaCategory")
    names = ("Produtos", "Serviços", "Tutoriais", "Mídias gerais", "Documentos", "Perguntas frequentes")
    for company_id in Company.objects.values_list("pk", flat=True).iterator():
        MediaCategory.objects.bulk_create(
            [MediaCategory(company_id=company_id, name=name) for name in names],
            ignore_conflicts=True,
        )


class Migration(migrations.Migration):
    dependencies = [("saas", "0006_hot_lead_notifications")]

    operations = [
        migrations.CreateModel(
            name="MediaCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="media_categories", to="saas.company")),
                ("parent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="children", to="saas.mediacategory")),
            ],
        ),
        migrations.CreateModel(
            name="MediaAsset",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=200)),
                ("description", models.TextField(max_length=4000)),
                ("tags", models.JSONField(blank=True, default=list)),
                ("keywords", models.JSONField(blank=True, default=list)),
                ("file", models.FileField(upload_to=saas.models.media_upload_path)),
                ("file_type", models.CharField(choices=[("image", "Imagem"), ("video", "Vídeo"), ("document", "Documento")], max_length=16)),
                ("mime_type", models.CharField(max_length=120)),
                ("size", models.PositiveBigIntegerField()),
                ("product_service", models.CharField(blank=True, max_length=200)),
                ("search_text", models.TextField(max_length=8000)),
                ("embedding", pgvector.django.vector.VectorField(blank=True, dimensions=768, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("allow_ai_send", models.BooleanField(default=False)),
                ("status", models.CharField(choices=[("pending", "Pendente"), ("ready", "Pronto"), ("failed", "Falhou")], default="pending", max_length=16)),
                ("error", models.TextField(blank=True)),
                ("meta_media_id", models.CharField(blank=True, max_length=128)),
                ("meta_media_status", models.CharField(choices=[("missing", "Não enviado"), ("valid", "Válido"), ("expired", "Expirado"), ("failed", "Falhou")], default="missing", max_length=16)),
                ("meta_media_updated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="assets", to="saas.mediacategory")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="media_assets", to="saas.company")),
            ],
        ),
        migrations.AddConstraint(
            model_name="mediacategory",
            constraint=models.UniqueConstraint(fields=("company", "parent", "name"), name="unique_company_media_category", nulls_distinct=False),
        ),
        migrations.AddField(
            model_name="message",
            name="media_asset",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="messages", to="saas.mediaasset"),
        ),
        migrations.AddField(model_name="message", name="source_media_id", field=models.CharField(blank=True, max_length=128)),
        migrations.AddField(model_name="message", name="transcription", field=models.TextField(blank=True)),
        migrations.RunPython(create_default_categories, migrations.RunPython.noop),
    ]
