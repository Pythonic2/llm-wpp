from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import (
    Company,
    Contact,
    Conversation,
    KnowledgeBase,
    KnowledgeDocument,
    Membership,
    Message,
    WhatsAppNumber,
)

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "email", "first_name", "last_name")


class MembershipSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ("id", "user", "role")


class CompanySerializer(serializers.ModelSerializer):
    role = serializers.CharField(read_only=True)

    class Meta:
        model = Company
        fields = ("id", "name", "slug", "created_at", "role")
        read_only_fields = fields


class ContactSerializer(serializers.ModelSerializer):
    wa_id = serializers.CharField(required=True)

    class Meta:
        model = Contact
        fields = ("id", "name", "phone", "wa_id", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")
        validators = ()

    def get_fields(self):
        fields = super().get_fields()
        if self.instance is not None:
            fields["wa_id"].read_only = True
        return fields

    def validate_wa_id(self, value):
        company = self.context.get("company")
        if company and Contact.objects.filter(company=company, wa_id=value).exclude(
            pk=getattr(self.instance, "pk", None)
        ).exists():
            raise serializers.ValidationError("Já existe um contato com este WhatsApp ID.")
        return value


class WhatsAppNumberSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsAppNumber
        fields = (
            "id",
            "phone_number_id",
            "display_phone_number",
            "business_account_id",
            "ai_enabled",
            "is_active",
            "created_at",
        )
        read_only_fields = ("id", "created_at")


class ConversationSerializer(serializers.ModelSerializer):
    contact = ContactSerializer(read_only=True)
    assigned_to = UserSerializer(read_only=True)
    assigned_to_id = serializers.PrimaryKeyRelatedField(
        source="assigned_to", queryset=User.objects.all(), write_only=True, allow_null=True, required=False
    )
    whatsapp_number = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Conversation
        fields = (
            "id",
            "contact",
            "whatsapp_number",
            "status",
            "lead_stage",
            "lead_data",
            "lead_temperature",
            "purchase_intent_score",
            "analysis_summary",
            "buying_signals",
            "objections",
            "recommended_action",
            "analyzed_at",
            "opportunity_status",
            "next_follow_up_at",
            "follow_up_completed_at",
            "ai_paused",
            "ai_paused_at",
            "assigned_to",
            "assigned_to_id",
            "last_message_at",
            "created_at",
        )
        read_only_fields = (
            "id", "contact", "whatsapp_number", "lead_temperature", "purchase_intent_score",
            "analysis_summary", "buying_signals", "objections", "recommended_action", "analyzed_at",
            "opportunity_status", "next_follow_up_at", "follow_up_completed_at", "ai_paused_at",
            "last_message_at", "created_at",
        )


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = (
            "id",
            "external_id",
            "direction",
            "message_type",
            "text",
            "status",
            "ai_status",
            "response_to",
            "created_at",
        )
        read_only_fields = fields


class KnowledgeBaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeBase
        fields = ("id", "name", "is_active", "created_at")
        read_only_fields = ("id", "created_at")


class KnowledgeDocumentSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True, required=False)
    file_name = serializers.SerializerMethodField()

    class Meta:
        model = KnowledgeDocument
        fields = ("id", "title", "file", "file_name", "status", "error", "created_at")
        read_only_fields = ("id", "file_name", "status", "error", "created_at")

    def get_file_name(self, obj):
        return obj.file.name.rsplit("/", 1)[-1] if obj.file else ""

    def validate_file(self, value):
        if value.name.rsplit(".", 1)[-1].lower() not in {"pdf", "txt", "csv"}:
            raise serializers.ValidationError("Use PDF, TXT ou CSV.")
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get("file"):
            raise serializers.ValidationError({"file": "Este campo é obrigatório."})
        return attrs

    def create(self, validated_data):
        validated_data["company_id"] = self.context["company_id"]
        validated_data["knowledge_base"] = self.context["knowledge_base"]
        document = KnowledgeDocument(**validated_data)
        try:
            document.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        document.save()
        return document
