import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from pgvector.django import VectorField


DEFAULT_AI_INSTRUCTIONS = """Atue como uma pessoa da equipe de atendimento da empresa e responda em português do Brasil.
Seja natural, cordial, direto e prestativo. Responda como quem conhece a empresa e adapte o nível de detalhe à dúvida do cliente.
Não diga nem insinue que você consultou documentos, um sistema, uma base de conhecimento ou um contexto fornecido."""


class Company(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    slug = models.SlugField(unique=True)
    ai_instructions = models.TextField(default=DEFAULT_AI_INSTRUCTIONS, max_length=8000)
    users = models.ManyToManyField(settings.AUTH_USER_MODEL, through="Membership", related_name="companies")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self): return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Proprietário"
        ADMIN = "admin", "Administrador"
        AGENT = "agent", "Atendente"
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.AGENT)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "user"], name="unique_company_user")]


class WhatsAppNumber(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="whatsapp_numbers")
    phone_number_id = models.CharField(max_length=64, unique=True, help_text="ID do número na Meta")
    display_phone_number = models.CharField(max_length=32)
    business_account_id = models.CharField(max_length=64, blank=True)
    ai_enabled = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"{self.display_phone_number} — {self.company}"


class Contact(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="contacts")
    wa_id = models.CharField(max_length=64)
    name = models.CharField(max_length=160, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "wa_id"], name="unique_company_contact")]

    def __str__(self): return self.name or self.wa_id


class Conversation(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Aberta"
        QUALIFYING = "qualifying", "Em qualificação"
        QUALIFIED = "qualified", "Qualificada"
        CLOSED = "closed", "Encerrada"
    class LeadTemperature(models.TextChoices):
        UNKNOWN = "unknown", "Não analisada"
        HOT = "hot", "Quente"
        WARM = "warm", "Morna"
        COLD = "cold", "Fria"
    class OpportunityStatus(models.TextChoices):
        OPEN = "open", "Em aberto"
        WON = "won", "Ganha"
        LOST = "lost", "Perdida"
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="conversations")
    whatsapp_number = models.ForeignKey(WhatsAppNumber, on_delete=models.PROTECT, related_name="conversations")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="conversations")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    lead_stage = models.CharField(max_length=80, blank=True)
    lead_data = models.JSONField(default=dict, blank=True)
    lead_temperature = models.CharField(
        max_length=12, choices=LeadTemperature.choices, default=LeadTemperature.UNKNOWN
    )
    purchase_intent_score = models.PositiveSmallIntegerField(default=0)
    analysis_summary = models.TextField(blank=True)
    buying_signals = models.JSONField(default=list, blank=True)
    objections = models.JSONField(default=list, blank=True)
    recommended_action = models.CharField(max_length=500, blank=True)
    analyzed_at = models.DateTimeField(null=True, blank=True)
    opportunity_status = models.CharField(
        max_length=12, choices=OpportunityStatus.choices, default=OpportunityStatus.OPEN
    )
    next_follow_up_at = models.DateTimeField(null=True, blank=True)
    follow_up_completed_at = models.DateTimeField(null=True, blank=True)
    ai_paused = models.BooleanField(default=False, help_text="Pausa respostas automáticas para atendimento humano")
    ai_paused_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_conversations")
    last_message_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "whatsapp_number", "contact"], name="unique_number_contact_conversation")]

    def clean(self):
        if self.whatsapp_number_id and self.whatsapp_number.company_id != self.company_id: raise ValidationError("Número pertence a outra empresa.")
        if self.contact_id and self.contact.company_id != self.company_id: raise ValidationError("Contato pertence a outra empresa.")


class Message(models.Model):
    class Direction(models.TextChoices):
        INBOUND = "in", "Recebida"
        OUTBOUND = "out", "Enviada"
    class Status(models.TextChoices):
        RECEIVED = "received", "Recebida"
        PENDING = "pending", "Pendente"
        SENT = "sent", "Enviada"
        DELIVERED = "delivered", "Entregue"
        READ = "read", "Lida"
        FAILED = "failed", "Falhou"
    class AIStatus(models.TextChoices):
        NOT_APPLICABLE = "na", "Não aplicável"
        QUEUED = "queued", "Na fila"
        PROCESSING = "processing", "Processando"
        COMPLETED = "completed", "Concluído"
        SKIPPED = "skipped", "Ignorado"
        FAILED = "failed", "Falhou"
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="messages")
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    external_id = models.CharField(max_length=128, unique=True)
    idempotency_key = models.CharField(max_length=128, unique=True, null=True, blank=True)
    direction = models.CharField(max_length=3, choices=Direction.choices)
    message_type = models.CharField(max_length=32, default="text")
    text = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECEIVED)
    raw_payload = models.JSONField(default=dict, blank=True)
    ai_status = models.CharField(max_length=16, choices=AIStatus.choices, default=AIStatus.NOT_APPLICABLE)
    ai_error = models.TextField(blank=True)
    response_to = models.OneToOneField("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="ai_response")
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.conversation_id and self.conversation.company_id != self.company_id: raise ValidationError("Conversa pertence a outra empresa.")


def knowledge_upload_path(instance, filename):
    return f"companies/{instance.company_id}/knowledge/{uuid.uuid4()}-{filename}"


class KnowledgeBase(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="knowledge_bases")
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_company_knowledge_base")]
    def __str__(self): return f"{self.name} — {self.company}"


class KnowledgeDocument(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        PROCESSING = "processing", "Processando"
        READY = "ready", "Pronto"
        FAILED = "failed", "Falhou"
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="knowledge_documents")
    knowledge_base = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to=knowledge_upload_path)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.knowledge_base_id and self.knowledge_base.company_id != self.company_id: raise ValidationError("Base pertence a outra empresa.")
        if self.file and self.file.name.rsplit(".", 1)[-1].lower() not in {"pdf", "txt", "csv"}: raise ValidationError("Use PDF, TXT ou CSV.")


class KnowledgeChunk(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="knowledge_chunks")
    knowledge_base = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="chunks")
    document = models.ForeignKey(KnowledgeDocument, on_delete=models.CASCADE, related_name="chunks")
    position = models.PositiveIntegerField()
    content = models.TextField()
    embedding = VectorField(dimensions=768)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["document", "position"], name="unique_document_chunk_position")]
