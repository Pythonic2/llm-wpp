from django.db import migrations, models


DEFAULT_AI_INSTRUCTIONS = """Atue como uma pessoa da equipe de atendimento da empresa e responda em português do Brasil.
Seja natural, cordial, direto e prestativo. Responda como quem conhece a empresa e adapte o nível de detalhe à dúvida do cliente.
Não diga nem insinue que você consultou documentos, um sistema, uma base de conhecimento ou um contexto fornecido."""


class Migration(migrations.Migration):
    dependencies = [("saas", "0004_conversation_sales_analysis")]

    operations = [
        migrations.AddField(
            model_name="company",
            name="ai_instructions",
            field=models.TextField(default=DEFAULT_AI_INSTRUCTIONS, max_length=8000),
        ),
    ]
