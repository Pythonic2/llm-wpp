from django.contrib import admin
from django.db import transaction
from django.utils import timezone

from .models import Company, Contact, Conversation, KnowledgeBase, KnowledgeChunk, KnowledgeDocument, Membership, Message, WhatsAppNumber
from .tasks import process_document


class TenantAdmin(admin.ModelAdmin):
    company_field = "company"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser: return qs
        return qs.filter(**{f"{self.company_field}__memberships__user": request.user}).distinct()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and db_field.name in {"company", "knowledge_base", "contact", "conversation", "whatsapp_number", "document"}:
            model = db_field.remote_field.model
            if model is Company: kwargs["queryset"] = Company.objects.filter(memberships__user=request.user)
            elif hasattr(model, "company_id"): kwargs["queryset"] = model.objects.filter(company__memberships__user=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "hot_lead_notifications_enabled", "created_at")
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(memberships__user=request.user).distinct()


@admin.register(Membership)
class MembershipAdmin(TenantAdmin):
    list_display = ("company", "user", "role")


@admin.register(WhatsAppNumber)
class WhatsAppNumberAdmin(TenantAdmin):
    list_display = ("display_phone_number", "company", "ai_enabled", "is_active")
    list_editable = ("ai_enabled", "is_active")


@admin.register(Contact)
class ContactAdmin(TenantAdmin):
    list_display = ("name", "wa_id", "company", "updated_at")
    search_fields = ("name", "wa_id")


@admin.register(Conversation)
class ConversationAdmin(TenantAdmin):
    list_display = ("contact", "company", "status", "ai_paused", "assigned_to", "lead_stage", "last_message_at")
    list_editable = ("ai_paused", "assigned_to")
    list_filter = ("status", "ai_paused", "company")

    def save_model(self, request, obj, form, change):
        if "ai_paused" in form.changed_data:
            obj.ai_paused_at = timezone.now() if obj.ai_paused else None
        super().save_model(request, obj, form, change)


@admin.register(Message)
class MessageAdmin(TenantAdmin):
    list_display = ("external_id", "company", "direction", "status", "ai_status", "created_at")
    readonly_fields = ("external_id", "raw_payload", "ai_status", "ai_error", "response_to", "created_at")


@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(TenantAdmin):
    list_display = ("name", "company", "is_active")


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(TenantAdmin):
    list_display = ("title", "company", "knowledge_base", "status", "created_at")
    readonly_fields = ("status", "error")

    def save_model(self, request, obj, form, change):
        file_changed = "file" in form.changed_data
        super().save_model(request, obj, form, change)
        if file_changed:
            transaction.on_commit(lambda: process_document.delay(obj.pk))


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(TenantAdmin):
    list_display = ("document", "position", "company")
    readonly_fields = ("company", "knowledge_base", "document", "position", "content", "embedding", "created_at")
    def has_add_permission(self, request): return False
