from datetime import timedelta

from django.contrib import admin, messages
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    BillingCheckout, BillingCustomer, BillingEvent, BusinessUnit, BusinessUnitEvent,
    BusinessUnitReview, CatalogCategory, CatalogItem,
    CoexistenceWebhookBatch, Company, Contact, Conversation, EmbeddedSignupAttempt,
    FollowUpAttempt, KnowledgeBase, KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentShare, MediaAsset,
    MediaCategory, Membership, Message, Order, OrderStatusEvent, PaymentMethod, PlanPriceRevision, PlanVersion,
    Sale, SaleEvent, SaleExpense, SaleLine, SalePayment, StaffPasswordState,
    WhatsAppBusinessAccount, WhatsAppNumber,
)
from .billing_pricing import PRICE_FIELDS, plan_prices, record_price_revision
from .tasks import process_document, process_media_asset


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


class SuperuserMutationAdmin(TenantAdmin):
    def has_add_permission(self, request):
        return request.user.is_superuser and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_delete_permission(request, obj)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = (
        "name", "slug", "billing_status", "trial_expires_at", "archived_at",
        "hot_lead_notifications_enabled", "created_at",
    )
    list_filter = ("billing_status", "archived_at")
    search_fields = ("name", "slug")
    actions = ("grant_trial_7", "grant_trial_14", "end_trial", "archive_companies", "restore_companies")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(memberships__user=request.user).distinct()

    def get_readonly_fields(self, request, obj=None):
        fields = ("trial_expires_at", "archived_at", "created_at")
        if not request.user.is_superuser:
            fields += ("billing_status",)
        return fields

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            for name in self.actions:
                actions.pop(name, None)
            actions.pop("delete_selected", None)
        return actions

    def has_delete_permission(self, request, obj=None):
        # Preserve sales and billing history; use archive_companies instead.
        return False

    def has_add_permission(self, request):
        # Company creation controls billing entitlement.
        return request.user.is_superuser and super().has_add_permission(request)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        initial["billing_status"] = Company.BillingStatus.PENDING
        return initial

    def _change_companies(self, request, queryset, *, operation, days=None):
        if not request.user.is_superuser:
            self.message_user(request, "Somente superusuários podem alterar o acesso.", messages.ERROR)
            return
        now = timezone.now()
        count = 0
        with transaction.atomic():
            for company in queryset.select_for_update():
                if operation == "grant":
                    if company.billing_status == Company.BillingStatus.LEGACY:
                        company.billing_status = Company.BillingStatus.PENDING
                        fields = ["billing_status", "trial_expires_at"]
                    else:
                        fields = ["trial_expires_at"]
                    company.trial_expires_at = now + timedelta(days=days)
                    company.save(update_fields=fields)
                    description = f"Teste de {days} dias concedido até {company.trial_expires_at.isoformat()}"
                elif operation == "end":
                    company.trial_expires_at = None
                    company.save(update_fields=["trial_expires_at"])
                    description = "Teste encerrado manualmente"
                elif operation == "archive":
                    company.archived_at = now
                    company.save(update_fields=["archived_at"])
                    description = "Empresa arquivada; histórico preservado"
                else:
                    company.archived_at = None
                    company.save(update_fields=["archived_at"])
                    description = "Empresa restaurada"
                self.log_change(request, company, description)
                count += 1
        self.message_user(request, f"{count} empresa(s) atualizada(s).", messages.SUCCESS)
        if operation == "grant":
            self.message_user(request, "Contas de usuário inativas ainda precisam ser ativadas no Admin para entrar.", messages.WARNING)
        if operation == "archive":
            self.message_user(request, "Arquivar não cancela cobrança no Asaas. Confira os checkouts e assinaturas da empresa no provedor.", messages.WARNING)

    @admin.action(description="Conceder teste por 7 dias")
    def grant_trial_7(self, request, queryset):
        self._change_companies(request, queryset, operation="grant", days=7)

    @admin.action(description="Conceder teste por 14 dias")
    def grant_trial_14(self, request, queryset):
        self._change_companies(request, queryset, operation="grant", days=14)

    @admin.action(description="Encerrar teste")
    def end_trial(self, request, queryset):
        self._change_companies(request, queryset, operation="end")

    @admin.action(description="Arquivar empresas selecionadas")
    def archive_companies(self, request, queryset):
        self._change_companies(request, queryset, operation="archive")

    @admin.action(description="Restaurar empresas selecionadas")
    def restore_companies(self, request, queryset):
        self._change_companies(request, queryset, operation="restore")


class BillingAuditAdmin(admin.ModelAdmin):
    """Expose provider records for support without forging payment state."""

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)


@admin.register(BillingCheckout)
class BillingCheckoutAdmin(BillingAuditAdmin):
    list_display = ("id", "company", "user", "kind", "status", "asaas_environment", "created_at")
    list_filter = ("kind", "status", "asaas_environment")
    search_fields = ("company__name", "user__email", "asaas_checkout_id", "asaas_subscription_id")


@admin.register(BillingCustomer)
class BillingCustomerAdmin(BillingAuditAdmin):
    list_display = ("user", "asaas_environment", "email_verified_at", "created_at")
    search_fields = ("user__email", "asaas_customer_id")
    exclude = ("cpf_cnpj", "mobile_phone", "postal_code", "address", "address_number", "complement", "province", "city_name", "state")


@admin.register(BillingEvent)
class BillingEventAdmin(BillingAuditAdmin):
    list_display = ("event_id", "event_type", "checkout", "received_at")
    search_fields = ("event_id", "checkout__id")


@admin.register(Sale)
class SaleAdmin(BillingAuditAdmin):
    list_display = ("id", "company", "contact", "status", "total", "sold_at")
    list_filter = ("status",)
    search_fields = ("company__name", "contact__name", "external_reference")


@admin.register(SaleLine, SaleExpense, SalePayment, SaleEvent)
class SaleDetailAdmin(BillingAuditAdmin):
    list_display = ("id", "sale")


@admin.register(Order)
class OrderAdmin(BillingAuditAdmin):
    list_display = ("id", "company", "status", "total", "created_at")
    list_filter = ("status",)
    search_fields = ("company__name", "id")


@admin.register(OrderStatusEvent)
class OrderStatusEventAdmin(BillingAuditAdmin):
    list_display = ("id", "company", "order", "previous_status", "new_status", "created_at")


@admin.register(FollowUpAttempt)
class FollowUpAttemptAdmin(BillingAuditAdmin):
    list_display = ("id", "company", "status", "created_at")
    list_filter = ("status",)


@admin.register(EmbeddedSignupAttempt, WhatsAppBusinessAccount, CoexistenceWebhookBatch, StaffPasswordState)
class IntegrationAuditAdmin(BillingAuditAdmin):
    pass


@admin.register(BusinessUnitEvent, KnowledgeDocumentShare, PlanPriceRevision)
class UnitAuditAdmin(BillingAuditAdmin):
    pass


@admin.register(PlanVersion)
class PlanVersionAdmin(admin.ModelAdmin):
    list_display = ("code", "version", "name", "first_company_monthly_price", "extra_company_monthly_price", "additional_whatsapp_monthly_price", "active_subscriptions")
    readonly_fields = ("created_at", "active_subscriptions")
    fields = ("code", "version", "name", "included_business_units", "included_whatsapp_numbers",
              "first_company_monthly_price", "extra_company_monthly_price", "additional_whatsapp_monthly_price",
              "reason", "active_subscriptions", "created_at")

    class Form(forms.ModelForm):
        reason = forms.CharField(label="Motivo da alteração", required=False, widget=forms.Textarea(attrs={"rows": 3}),
                                 help_text="Obrigatório ao alterar preços. O novo valor vale somente para novas contratações; assinaturas existentes mantêm o valor contratado.")

        class Meta:
            model = PlanVersion
            fields = "__all__"

        def clean(self):
            data = super().clean()
            changed_prices = any(field in self.changed_data for field in PRICE_FIELDS)
            if (changed_prices or not self.instance.pk) and len((data.get("reason") or "").strip()) < 10:
                raise ValidationError("Informe um motivo de pelo menos 10 caracteres para a mudança de preços.")
            return data

    form = Form

    def has_module_permission(self, request): return request.user.is_superuser
    def has_view_permission(self, request, obj=None): return request.user.is_superuser
    def has_add_permission(self, request): return request.user.is_superuser
    def has_change_permission(self, request, obj=None): return request.user.is_superuser
    def has_delete_permission(self, request, obj=None): return False

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields + (("code", "version") if obj else ())

    @admin.display(description="Assinaturas pagas")
    def active_subscriptions(self, obj):
        if not obj or not obj.pk:
            return 0
        return BillingCheckout.objects.filter(company__plan=obj, status=BillingCheckout.Status.PAID).count()

    def save_model(self, request, obj, form, change):
        previous = plan_prices(PlanVersion.objects.get(pk=obj.pk)) if change else {}
        super().save_model(request, obj, form, change)
        revision = record_price_revision(obj, request.user, form.cleaned_data["reason"], previous)
        if revision:
            self.message_user(request, "Preço salvo para novas contratações. Assinaturas existentes mantêm o valor contratado.", messages.SUCCESS)


@admin.register(BusinessUnit)
class BusinessUnitAdmin(BillingAuditAdmin):
    list_display = ("name", "company", "is_default", "is_active", "created_at")
    search_fields = ("name", "company__name")


@admin.register(BusinessUnitReview)
class BusinessUnitReviewAdmin(admin.ModelAdmin):
    list_display = ("company", "business_unit", "status", "created_at", "decided_at")
    list_filter = ("status",)
    readonly_fields = ("decided_by", "decided_at", "created_at")

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    class Form(forms.ModelForm):
        class Meta:
            model = BusinessUnitReview
            fields = "__all__"

        def clean(self):
            data = super().clean()
            company, unit = data.get("company"), data.get("business_unit")
            if company and unit and unit.company_id != company.pk:
                raise ValidationError("Unidade pertence a outra empresa.")
            if data.get("status") != BusinessUnitReview.Status.OPEN and not data.get("decision_reason", "").strip():
                raise ValidationError("Informe a justificativa da decisão.")
            return data

    form = Form

    def save_model(self, request, obj, form, change):
        if obj.status != BusinessUnitReview.Status.OPEN:
            obj.decided_by = request.user
            obj.decided_at = timezone.now()
        super().save_model(request, obj, form, change)
        BusinessUnitEvent.objects.create(
            company=obj.company, business_unit=obj.business_unit, actor=request.user,
            kind="review_decision", details={"review_id": obj.pk, "status": obj.status},
        )


class ManualDataAdmin(admin.ModelAdmin):
    """Simple company data editable by the operator, with no billing side effects."""

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(CatalogCategory)
class CatalogCategoryAdmin(ManualDataAdmin):
    list_display = ("name", "company", "is_active")
    search_fields = ("name", "company__name")


@admin.register(PaymentMethod)
class PaymentMethodAdmin(ManualDataAdmin):
    list_display = ("name", "company", "kind", "is_active")
    search_fields = ("name", "company__name")


@admin.register(CatalogItem)
class CatalogItemAdmin(ManualDataAdmin):
    list_display = ("name", "company", "kind", "price_mode", "is_active")
    search_fields = ("name", "sku", "company__name")

    class Form(forms.ModelForm):
        class Meta:
            model = CatalogItem
            fields = "__all__"

        def clean(self):
            data = super().clean()
            company = data.get("company")
            unit = data.get("business_unit")
            category = data.get("category")
            assets = data.get("media_assets")
            if company and category and category.company_id != company.pk:
                raise ValidationError("Categoria pertence a outra empresa.")
            if company and assets is not None and assets.exclude(company_id=company.pk).exists():
                raise ValidationError("Mídia pertence a outra empresa.")
            if unit and assets is not None and assets.exclude(business_unit_id=unit.pk).exists():
                raise ValidationError("Mídia pertence a outra unidade.")
            return data

    form = Form


@admin.register(Membership)
class MembershipAdmin(SuperuserMutationAdmin):
    list_display = ("company", "user", "role")


@admin.register(WhatsAppNumber)
class WhatsAppNumberAdmin(SuperuserMutationAdmin):
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


@admin.register(MediaCategory)
class MediaCategoryAdmin(TenantAdmin):
    list_display = ("name", "company", "parent", "updated_at")
    search_fields = ("name",)


@admin.register(MediaAsset)
class MediaAssetAdmin(TenantAdmin):
    list_display = ("name", "company", "file_type", "status", "is_active", "allow_ai_send", "updated_at")
    list_filter = ("file_type", "status", "is_active", "allow_ai_send")
    search_fields = ("name", "description", "product_service", "search_text")
    readonly_fields = ("mime_type", "size", "status", "error", "embedding", "meta_media_id", "meta_media_status")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change or any(field in form.changed_data for field in ("name", "description", "tags", "keywords", "product_service", "search_text")):
            transaction.on_commit(lambda: process_media_asset.delay(obj.pk))
