from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import (
    Company,
    Contact,
    Conversation,
    KnowledgeBase,
    KnowledgeDocument,
    MediaAsset,
    MediaCategory,
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


class CompanyAISettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = (
            "ai_instructions",
            "hot_lead_notifications_enabled",
            "hot_lead_notification_phone",
        )

    def validate_ai_instructions(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Escreva pelo menos uma instrução para o atendimento.")
        return value

    def validate_hot_lead_notification_phone(self, value):
        value = value.strip()
        digits = "".join(character for character in value if character.isdigit())
        if value and not 8 <= len(digits) <= 15:
            raise serializers.ValidationError("Informe um WhatsApp válido, com DDI e DDD.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        enabled = attrs.get(
            "hot_lead_notifications_enabled",
            getattr(self.instance, "hot_lead_notifications_enabled", False),
        )
        phone = attrs.get(
            "hot_lead_notification_phone",
            getattr(self.instance, "hot_lead_notification_phone", ""),
        )
        if enabled and not phone:
            raise serializers.ValidationError(
                {"hot_lead_notification_phone": "Cadastre o WhatsApp que receberá os alertas."}
            )
        return attrs


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
            "transcription",
            "source_media_id",
            "media_asset",
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
        if self.instance is not None and attrs.get("file"):
            raise serializers.ValidationError({"file": "Crie uma nova mídia para substituir o arquivo."})
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


class MediaCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaCategory
        fields = ("id", "name", "parent", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_parent(self, value):
        company = self.context["company"]
        if value and value.company_id != company.pk:
            raise serializers.ValidationError("Categoria pai pertence a outra empresa.")
        return value

    def create(self, validated_data):
        validated_data["company"] = self.context["company"]
        return super().create(validated_data)


MEDIA_MIME_TYPES = {
    "image/jpeg": MediaAsset.FileType.IMAGE,
    "image/png": MediaAsset.FileType.IMAGE,
    "image/webp": MediaAsset.FileType.IMAGE,
    "image/gif": MediaAsset.FileType.IMAGE,
    "video/mp4": MediaAsset.FileType.VIDEO,
    "video/3gpp": MediaAsset.FileType.VIDEO,
    "application/pdf": MediaAsset.FileType.DOCUMENT,
}


def _sniff_media_mime(upload):
    header = upload.read(16)
    upload.seek(0)
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header[:4] == b"GIF8":
        return "image/gif"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    if header.startswith(b"%PDF-"):
        return "application/pdf"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/3gpp" if header[8:11] == b"3gp" else "video/mp4"
    return ""


class MediaAssetSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True, required=False)
    category_name = serializers.CharField(source="category.name", read_only=True)
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = (
            "id", "name", "description", "category", "category_name", "tags", "keywords",
            "file", "download_url", "file_type", "mime_type", "size", "product_service",
            "search_text", "is_active", "allow_ai_send", "status", "error",
            "meta_media_status", "created_at", "updated_at",
        )
        read_only_fields = (
            "id", "download_url", "file_type", "mime_type", "size", "status", "error",
            "meta_media_status", "created_at", "updated_at",
        )

    def get_download_url(self, obj):
        # Keep the URL on the caller's origin. An absolute URI here can expose
        # the reverse proxy's internal host and make browsers bypass the
        # frontend proxy that owns authentication and routing.
        return f"/api/v1/companies/{obj.company_id}/media-assets/{obj.pk}/download/"

    def validate_category(self, value):
        company = self.context["company"]
        if value and value.company_id != company.pk:
            raise serializers.ValidationError("Categoria pertence a outra empresa.")
        return value

    def validate_tags(self, value):
        if not isinstance(value, list) or len(value) > 30:
            raise serializers.ValidationError("Informe uma lista com no máximo 30 tags.")
        return [str(item).strip()[:80] for item in value if str(item).strip()]

    validate_keywords = validate_tags

    def validate_file(self, value):
        if value.size > settings.MEDIA_LIBRARY_MAX_BYTES:
            raise serializers.ValidationError("Arquivo maior que o limite permitido.")
        detected = _sniff_media_mime(value)
        declared = str(getattr(value, "content_type", "")).lower()
        if detected not in MEDIA_MIME_TYPES or declared != detected:
            raise serializers.ValidationError("Formato ou MIME type inválido.")
        value.detected_mime_type = detected
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get("file"):
            raise serializers.ValidationError({"file": "Este campo é obrigatório."})
        return attrs

    def create(self, validated_data):
        upload = validated_data["file"]
        mime_type = upload.detected_mime_type
        validated_data.update(
            company=self.context["company"],
            mime_type=mime_type,
            file_type=MEDIA_MIME_TYPES[mime_type],
            size=upload.size,
            status=MediaAsset.Status.PENDING,
        )
        return super().create(validated_data)
