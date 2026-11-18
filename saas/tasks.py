import hashlib
import json
import logging
import mimetypes
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from celery import shared_task
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from redis.exceptions import LockError

from .models import CoexistenceWebhookBatch, Company, Conversation, FollowUpAttempt, KnowledgeChunk, KnowledgeDocument, MediaAsset, Message, Order
from .coexistence import process_app_history
from .services import (
    answer_with_rag,
    analyze_sales_conversation,
    conversation_ai_lock,
    download_whatsapp_audio,
    download_whatsapp_attachment,
    embed_texts,
    extract_text,
    extract_hot_lead_details,
    normalize_whatsapp_recipient,
    select_media_for_question,
    send_whatsapp_media,
    send_whatsapp_media_id,
    send_whatsapp_text,
    split_text,
    transcribe_audio,
)

logger = logging.getLogger(__name__)


@shared_task
def scan_warm_follow_ups():
    """Schedule only recent warm conversations; the worker checks every rule again."""
    from .follow_up import BUSINESS_TIMEZONE

    now = timezone.now()
    FollowUpAttempt.objects.filter(
        status=FollowUpAttempt.Status.SENDING, created_at__lt=now - timedelta(minutes=15),
    ).update(status=FollowUpAttempt.Status.UNKNOWN, reason="Resultado incerto; verificar conversa antes de tentar novamente.")
    if not 9 <= now.astimezone(BUSINESS_TIMEZONE).hour < 18:
        return 0
    inbound_times = Message.objects.filter(
        conversation_id=OuterRef("pk"), direction=Message.Direction.INBOUND,
    ).annotate(event_at=Coalesce("sent_at", "created_at")).order_by("-event_at", "-pk").values("event_at")[:1]
    candidates = Conversation.objects.filter(
        company__warm_follow_up_enabled=True,
        lead_temperature=Conversation.LeadTemperature.WARM,
        opportunity_status=Conversation.OpportunityStatus.OPEN,
        ai_paused=False,
        assigned_to__isnull=True,
        contact__follow_up_consent_at__isnull=False,
        contact__follow_up_opted_out_at__isnull=True,
        whatsapp_number__is_active=True,
        whatsapp_number__ai_enabled=True,
    ).exclude(status=Conversation.Status.CLOSED).annotate(
        last_inbound_at=Subquery(inbound_times),
    ).filter(
        last_inbound_at__lte=now - timedelta(hours=20),
        last_inbound_at__gt=now - timedelta(hours=23),
    ).order_by("-last_inbound_at")[:200]
    ids = list(candidates.values_list("pk", flat=True))
    for conversation_id in ids:
        send_warm_follow_up.delay(conversation_id)
    return len(ids)


@shared_task
def send_warm_follow_up(conversation_id):
    """At most one Meta submission per inbound cycle; ambiguous crashes stay pending."""
    from .follow_up import DAILY_LIMIT, eligibility, generate_follow_up_text

    company_id = Conversation.objects.filter(pk=conversation_id).values_list("company_id", flat=True).first()
    if company_id is None:
        return
    with transaction.atomic():
        Company.objects.select_for_update().get(pk=company_id)
        conversation = Conversation.objects.select_for_update().select_related(
            "company", "contact", "whatsapp_number"
        ).get(pk=conversation_id)
        incoming, reason = eligibility(conversation)
        if incoming is None:
            return reason
        local_zone = ZoneInfo("America/Sao_Paulo")
        today = timezone.now().astimezone(local_zone).date()
        today_start = timezone.make_aware(
            datetime.combine(today, time.min), local_zone,
        )
        used = FollowUpAttempt.objects.filter(
            company_id=company_id, created_at__gte=today_start,
            status__in=(FollowUpAttempt.Status.SENDING, FollowUpAttempt.Status.SENT),
        ).count()
        if used >= DAILY_LIMIT:
            return "daily_limit"
        attempt, created = FollowUpAttempt.objects.get_or_create(
            company_id=company_id, conversation=conversation, trigger_message=incoming,
        )
        if not created:
            return "already_attempted"
    try:
        text = generate_follow_up_text(conversation)
        with transaction.atomic():
            conversation = Conversation.objects.select_for_update().select_related(
                "company", "contact", "whatsapp_number"
            ).get(pk=conversation_id)
            attempt.refresh_from_db()
            if attempt.status != FollowUpAttempt.Status.SENDING:
                return "not_sending"
            if Message.objects.filter(
                conversation=conversation, created_at__gt=attempt.created_at,
            ).exclude(pk=attempt.trigger_message_id).exists():
                FollowUpAttempt.objects.filter(pk=attempt.pk).update(status=FollowUpAttempt.Status.SKIPPED, reason="conversation_changed")
                return "conversation_changed"
            incoming_again, reason = eligibility(conversation, ignore_attempt_id=attempt.pk)
            if incoming_again is None or incoming_again.pk != attempt.trigger_message_id:
                FollowUpAttempt.objects.filter(pk=attempt.pk).update(status=FollowUpAttempt.Status.SKIPPED, reason=reason or "new_inbound")
                return reason or "new_inbound"
            outgoing = Message.objects.create(
                company=conversation.company, conversation=conversation,
                external_id=f"warm-pending:{attempt.pk}", idempotency_key=f"warm:{attempt.pk}",
                direction=Message.Direction.OUTBOUND, message_type="text", text=text,
                status=Message.Status.PENDING,
            )
            attempt.outbound_message = outgoing
            attempt.text = text
            attempt.save(update_fields=("outbound_message", "text"))
        external_id = send_whatsapp_text(conversation.whatsapp_number, conversation.contact.wa_id, text)
    except Exception as exc:
        logger.exception("Falha no follow-up automático; conversation_id=%s", conversation_id)
        FollowUpAttempt.objects.filter(pk=attempt.pk).update(status=FollowUpAttempt.Status.FAILED, reason=type(exc).__name__[:160])
        if attempt.outbound_message_id:
            Message.objects.filter(pk=attempt.outbound_message_id).update(status=Message.Status.FAILED)
        return "failed"
    sent_at = timezone.now()
    Message.objects.filter(pk=outgoing.pk).update(external_id=external_id, status=Message.Status.SENT, sent_at=sent_at)
    FollowUpAttempt.objects.filter(pk=attempt.pk).update(status=FollowUpAttempt.Status.SENT, external_id=external_id, sent_at=sent_at)
    Conversation.objects.filter(pk=conversation_id, last_message_at__lt=sent_at).update(last_message_at=sent_at)
    return "sent"


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=8)
def reconcile_asaas_checkout(checkout_id):
    from .billing import reconcile_checkout
    return reconcile_checkout(checkout_id)


@shared_task
def reconcile_asaas_billing():
    """Daily safety net for delayed or missed renewal Webhooks."""
    from .models import BillingCheckout
    from .billing import asaas_environment
    for checkout_id in BillingCheckout.objects.filter(
        status=BillingCheckout.Status.PAID,
        asaas_environment=asaas_environment(),
    ).values_list("pk", flat=True).iterator(chunk_size=200):
        reconcile_asaas_checkout.delay(str(checkout_id))


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_coexistence_history(batch_id):
    with transaction.atomic():
        batch = CoexistenceWebhookBatch.objects.select_for_update().select_related("number__company").get(pk=batch_id)
        if batch.processed_at:
            return
        process_app_history(batch.number, batch.payload)
        batch.payload = {}
        batch.processed_at = timezone.now()
        batch.save(update_fields=["payload", "processed_at"])


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def store_inbound_attachment(message_id):
    message = Message.objects.get(pk=message_id)
    if message.attachment or not message.source_media_id or message.message_type not in {"document", "audio"}:
        return
    if message.attachment_error == "processing":
        return
    Message.objects.filter(pk=message.pk).update(attachment_error="processing")
    try:
        content, mime_type = download_whatsapp_attachment(
            message.source_media_id, message.conversation.whatsapp_number
        )
        payload = message.raw_payload.get(message.message_type, {})
        original_name = Path(str(payload.get("filename") or "")).name
        if not original_name:
            extension = mimetypes.guess_extension(mime_type.split(";", 1)[0].strip()) or ".bin"
            original_name = f"{message.message_type}-{message.pk}{extension}"
        message.attachment.save(original_name, ContentFile(content), save=False)
        message.attachment_name = original_name[:255]
        message.attachment_mime_type = mime_type[:120]
        message.attachment_size = len(content)
        message.attachment_error = ""
        message.save(update_fields=(
            "attachment", "attachment_name", "attachment_mime_type",
            "attachment_size", "attachment_error",
        ))
    except Exception as exc:
        Message.objects.filter(pk=message.pk).update(attachment_error=str(exc)[:2000])
        raise


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def analyze_conversation(conversation_id):
    analysis = analyze_sales_conversation(conversation_id)
    with transaction.atomic():
        conversation = Conversation.objects.select_for_update().select_related("company").get(pk=conversation_id)
        conversation.lead_temperature = analysis["temperature"]
        conversation.purchase_intent_score = analysis["score"]
        conversation.analysis_summary = analysis["summary"]
        conversation.buying_signals = analysis["buying_signals"]
        conversation.objections = analysis["objections"]
        conversation.recommended_action = analysis["recommended_action"]
        conversation.analyzed_at = timezone.now()
        should_notify_hot_lead = False
        if analysis["temperature"] != Conversation.LeadTemperature.HOT:
            conversation.hot_lead_notified_at = None
            conversation.hot_lead_notification_message_id = ""
            conversation.hot_lead_notification_fingerprint = ""
        elif (
            conversation.company.hot_lead_notifications_enabled
            and conversation.company.hot_lead_notification_phone
        ):
            should_notify_hot_lead = True
        if conversation.opportunity_status == Conversation.OpportunityStatus.OPEN:
            delays = {
                Conversation.LeadTemperature.HOT: timedelta(days=1),
                Conversation.LeadTemperature.WARM: timedelta(days=3),
            }
            delay = delays.get(conversation.lead_temperature)
            conversation.next_follow_up_at = (conversation.last_message_at or timezone.now()) + delay if delay else None
            conversation.follow_up_completed_at = None
        conversation.save(
            update_fields=[
                "lead_temperature", "purchase_intent_score", "analysis_summary", "buying_signals",
                "objections", "recommended_action", "analyzed_at", "next_follow_up_at",
                "follow_up_completed_at", "hot_lead_notified_at",
                "hot_lead_notification_message_id",
                "hot_lead_notification_fingerprint",
            ]
        )
        if analysis.get("order_requested"):
            source = Message.objects.filter(
                conversation=conversation, direction=Message.Direction.INBOUND
            ).order_by("-created_at", "-pk").first()
            if source is not None:
                order_data = analysis.get("order", {})
                try:
                    raw_total = order_data.get("total")
                    total = None if raw_total in (None, "") else Decimal(str(raw_total))
                except (InvalidOperation, TypeError, ValueError):
                    total = None
                order, created = Order.objects.get_or_create(
                    company=conversation.company,
                    conversation=conversation,
                    source_message=source,
                    defaults={
                        "business_unit_id": conversation.business_unit_id,
                        "items": order_data.get("items", []),
                        "total": total,
                        "requested_for": order_data.get("requested_for", ""),
                        "notes": order_data.get("notes", ""),
                        "summary": order_data.get("summary", "") or analysis["summary"],
                    },
                )
                if created:
                    transaction.on_commit(lambda order_id=order.pk: notify_pending_order.delay(str(order_id)))
        if should_notify_hot_lead:
            transaction.on_commit(lambda: notify_hot_lead.delay(conversation.pk))


def _pending_order_notification_text(order):
    contact = order.conversation.contact
    lines = [
        "🛒 *NOVO PEDIDO AGUARDANDO APROVAÇÃO*", "",
        f"Cliente: {contact.name or 'Não informado'}",
        f"WhatsApp: {contact.phone or contact.wa_id}",
        f"Empresa: {order.company.name}",
    ]
    for item in order.items:
        lines.append(f"• {item.get('quantity') or 'Quantidade não informada'} — {item.get('name', 'Item')}")
    if order.total is not None:
        lines.append(f"Valor informado: R$ {order.total}")
    if order.requested_for:
        lines.append(f"Data/prazo solicitado: {order.requested_for}")
    if order.notes:
        lines.append(f"Observações: {order.notes}")
    if order.summary:
        lines.append(f"Resumo: {order.summary}")
    lines.extend(["", f"Identificador: {order.pk}", "Acesse o Gest Tech para aprovar ou rejeitar."])
    return "\n".join(lines)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def notify_pending_order(order_id):
    order = Order.objects.select_related(
        "company", "conversation__contact", "conversation__whatsapp_number"
    ).get(pk=order_id)
    company = order.company
    if order.status != Order.Status.PENDING or not company.hot_lead_notification_phone:
        return
    text = _pending_order_notification_text(order)
    fingerprint = hashlib.sha256(text.encode()).hexdigest()
    if order.notification_message_id and order.notification_fingerprint == fingerprint:
        return
    external_id = send_whatsapp_text(
        order.conversation.whatsapp_number,
        normalize_whatsapp_recipient(company.hot_lead_notification_phone),
        text,
    )
    Order.objects.filter(pk=order.pk, status=Order.Status.PENDING).update(
        notification_message_id=external_id,
        notification_fingerprint=fingerprint,
    )


def _hot_lead_notification_text(conversation, details=None):
    contact = conversation.contact
    lines = [
        "🔥 *Novo lead quente*",
        "",
        f"Cliente: {contact.name or 'Não informado'}",
        f"WhatsApp: {contact.phone or contact.wa_id}",
        f"Intenção de compra: {conversation.purchase_intent_score}/100",
    ]
    details = details or {}
    lines.append(f"Resumo: {details.get('resumo') or conversation.analysis_summary or 'Sem resumo disponível.'}")
    informed = details.get("dados_informados", [])
    if informed:
        lines.extend(["", "*Dados informados pelo cliente:*", *[
            f"• {item['campo']}: {item['valor']}" for item in informed
        ]])
    sections = (
        ("Interesses", "interesses"),
        ("Detalhes do produto, serviço ou pedido", "detalhes_pedido"),
        ("Condições comerciais", "condicoes_comerciais"),
        ("Informações pendentes", "pendencias"),
    )
    for title, field in sections:
        values = details.get(field, [])
        if values:
            lines.extend(["", f"*{title}:*", *[f"• {value}" for value in values]])
    if conversation.buying_signals:
        lines.append("Sinais de compra: " + "; ".join(conversation.buying_signals[:3]))
    if conversation.objections:
        lines.append("Objeções: " + "; ".join(conversation.objections[:3]))
    if conversation.recommended_action:
        lines.append(f"Próxima ação: {conversation.recommended_action}")
    return "\n".join(lines)


def _split_whatsapp_notification(text, limit=4000):
    chunks, current = [], []
    current_size = 0
    for line in text.splitlines():
        extra = len(line) + (1 if current else 0)
        if current and current_size + extra > limit:
            chunks.append("\n".join(current))
            current, current_size = [], 0
        current.append(line)
        current_size += len(line) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append("\n".join(current))
    return chunks


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def notify_hot_lead(conversation_id):
    conversation = Conversation.objects.select_related(
        "company", "contact", "whatsapp_number"
    ).get(pk=conversation_id)
    company = conversation.company
    if (
        conversation.lead_temperature != Conversation.LeadTemperature.HOT
        or not company.hot_lead_notifications_enabled
        or not company.hot_lead_notification_phone
    ):
        return
    try:
        details = extract_hot_lead_details(conversation.pk)
    except Exception:
        logger.exception("Falha ao extrair detalhes do lead quente %s", conversation.pk)
        details = None
    normalized_phone = normalize_whatsapp_recipient(company.hot_lead_notification_phone)
    fingerprint_payload = details or {
        "summary": conversation.analysis_summary,
        "signals": conversation.buying_signals,
        "objections": conversation.objections,
        "action": conversation.recommended_action,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    configured_digits = "".join(
        character for character in company.hot_lead_notification_phone if character.isdigit()
    )
    if (
        conversation.hot_lead_notification_message_id
        and conversation.hot_lead_notification_fingerprint == fingerprint
        and normalized_phone == configured_digits
    ):
        return
    external_id = ""
    for chunk in _split_whatsapp_notification(_hot_lead_notification_text(conversation, details)):
        external_id = send_whatsapp_text(
            conversation.whatsapp_number,
            normalized_phone,
            chunk,
        )
    if normalized_phone != company.hot_lead_notification_phone:
        Company.objects.filter(pk=company.pk).update(hot_lead_notification_phone=normalized_phone)
    Conversation.objects.filter(pk=conversation.pk).update(
        hot_lead_notified_at=timezone.now(),
        hot_lead_notification_message_id=external_id,
        hot_lead_notification_fingerprint=fingerprint,
    )


def _payment_receipt_notification_text(incoming):
    conversation = incoming.conversation
    contact = conversation.contact
    media_label = "foto" if incoming.message_type == "image" else "PDF/arquivo"
    lines = [
        "🧾 *Arquivo recebido para validação*",
        "",
        f"Cliente: {contact.name or 'Não informado'}",
        f"WhatsApp: {contact.phone or contact.wa_id}",
        f"Tipo recebido: {media_label}",
        "Status: pagamento ainda não confirmado; validação pendente.",
    ]
    if incoming.text:
        lines.append(f"Mensagem do cliente: {incoming.text[:300]}")
    if conversation.analysis_summary:
        lines.append(f"Resumo da conversa: {conversation.analysis_summary[:500]}")
    return "\n".join(lines)[:1024]


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def forward_inbound_media_to_notification(incoming_message_id):
    incoming = Message.objects.select_related(
        "company", "conversation__contact", "conversation__whatsapp_number"
    ).get(pk=incoming_message_id)
    company = incoming.company
    if (
        incoming.message_type not in {"image", "document"}
        or not incoming.source_media_id
        or not company.hot_lead_notifications_enabled
        or not company.hot_lead_notification_phone
        or incoming.raw_payload.get("notification_forwarded_id")
    ):
        return
    media_payload = incoming.raw_payload.get(incoming.message_type, {})
    external_id = send_whatsapp_media_id(
        incoming.conversation.whatsapp_number,
        company.hot_lead_notification_phone,
        incoming.source_media_id,
        incoming.message_type,
        caption=_payment_receipt_notification_text(incoming),
        filename=media_payload.get("filename", ""),
    )
    raw_payload = {**incoming.raw_payload, "notification_forwarded_id": external_id}
    Message.objects.filter(pk=incoming.pk).update(raw_payload=raw_payload)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_document(document_id):
    document = KnowledgeDocument.objects.select_related("knowledge_base").get(pk=document_id)
    document.status, document.error = KnowledgeDocument.Status.PROCESSING, ""
    document.save(update_fields=["status", "error"])
    try:
        chunks = split_text(extract_text(document))
        if not chunks: raise ValueError("Documento vazio")
        vectors = embed_texts(chunks)
        with transaction.atomic():
            document.chunks.all().delete()
            KnowledgeChunk.objects.bulk_create([KnowledgeChunk(company_id=document.company_id, knowledge_base_id=document.knowledge_base_id, document=document, position=i, content=text, embedding=vector) for i, (text, vector) in enumerate(zip(chunks, vectors, strict=True))])
            document.status = KnowledgeDocument.Status.READY
            document.save(update_fields=["status"])
    except Exception as exc:
        document.status, document.error = KnowledgeDocument.Status.FAILED, str(exc)[:2000]
        document.save(update_fields=["status", "error"])
        raise


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_media_asset(asset_id):
    asset = MediaAsset.objects.get(pk=asset_id)
    try:
        semantic_text = "\n".join(
            part for part in (
                asset.name,
                asset.description,
                asset.product_service,
                " ".join(asset.tags),
                " ".join(asset.keywords),
                asset.search_text,
            ) if part
        )[:8000]
        asset.embedding = embed_texts([semantic_text], "RETRIEVAL_DOCUMENT")[0]
        asset.status = MediaAsset.Status.READY
        asset.error = ""
        asset.save(update_fields=("embedding", "status", "error", "updated_at"))
    except Exception as exc:
        asset.status = MediaAsset.Status.FAILED
        asset.error = str(exc)[:2000]
        asset.save(update_fields=("status", "error", "updated_at"))
        raise


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def send_selected_media(self, incoming_message_id, asset_id):
    incoming = Message.objects.select_related(
        "conversation__whatsapp_number", "conversation__contact"
    ).get(pk=incoming_message_id)
    asset = MediaAsset.objects.get(pk=asset_id, company_id=incoming.company_id)
    if Message.objects.filter(
        conversation_id=incoming.conversation_id,
        media_asset=asset,
        status=Message.Status.SENT,
        created_at__gte=timezone.now() - timedelta(hours=24),
    ).exists():
        return
    key = f"ai-media:{incoming.pk}:{asset.pk}"
    outgoing, _ = Message.objects.get_or_create(
        idempotency_key=key,
        defaults={
            "external_id": f"media-pending:{incoming.pk}:{asset.pk}",
            "company_id": incoming.company_id,
            "conversation_id": incoming.conversation_id,
            "direction": Message.Direction.OUTBOUND,
            "message_type": asset.file_type,
            "text": asset.name,
            "status": Message.Status.PENDING,
            "media_asset": asset,
        },
    )
    if outgoing.status == Message.Status.SENT:
        return
    try:
        external_id = send_whatsapp_media(
            incoming.conversation.whatsapp_number,
            incoming.conversation.contact.wa_id,
            asset,
        )
    except Exception as exc:
        outgoing.status = Message.Status.FAILED
        outgoing.raw_payload = {"error": str(exc)[:2000]}
        outgoing.save(update_fields=("status", "raw_payload"))
        raise
    outgoing.external_id = external_id
    outgoing.status = Message.Status.SENT
    outgoing.raw_payload = {}
    outgoing.save(update_fields=("external_id", "status", "raw_payload"))


@shared_task(bind=True, max_retries=120)
def answer_inbound_message(self, message_id):
    from .billing import number_billing_available
    incoming = Message.objects.select_related("conversation__whatsapp_number", "conversation__contact").get(pk=message_id)
    lock = conversation_ai_lock(incoming.company_id, incoming.conversation_id)
    if not lock.acquire(blocking=False):
        raise self.retry(countdown=2)

    try:
        incoming = Message.objects.select_related("conversation__whatsapp_number", "conversation__contact").get(pk=message_id)
        if incoming.ai_status in {Message.AIStatus.COMPLETED, Message.AIStatus.SKIPPED, Message.AIStatus.FAILED}:
            return

        first_waiting = (
            Message.objects.filter(
                conversation_id=incoming.conversation_id,
                direction=Message.Direction.INBOUND,
                ai_status__in=[Message.AIStatus.QUEUED, Message.AIStatus.PROCESSING],
            )
            .order_by("created_at", "pk")
            .values_list("pk", flat=True)
            .first()
        )
        if first_waiting != incoming.pk:
            raise self.retry(countdown=2)

        conversation, number = incoming.conversation, incoming.conversation.whatsapp_number
        if not number_billing_available(number):
            incoming.ai_status = Message.AIStatus.SKIPPED
            incoming.save(update_fields=["ai_status"])
            return
        if incoming.message_type in {"image", "document"}:
            forward_inbound_media_to_notification.delay(incoming.pk)
        if conversation.ai_paused or not number.ai_enabled or not number.is_active or incoming.message_type not in {"text", "audio", "image", "document"}:
            incoming.ai_status = Message.AIStatus.SKIPPED
            incoming.save(update_fields=["ai_status"])
            return

        existing_response = Message.objects.filter(response_to=incoming).first()
        if existing_response:
            incoming.ai_status = Message.AIStatus.COMPLETED if existing_response.status == Message.Status.SENT else Message.AIStatus.FAILED
            incoming.ai_error = "Resposta já reservada; reenvio automático bloqueado para evitar duplicidade."
            incoming.save(update_fields=["ai_status", "ai_error"])
            return

        incoming.ai_status, incoming.ai_error = Message.AIStatus.PROCESSING, ""
        incoming.save(update_fields=["ai_status", "ai_error"])
        selected_media = None
        try:
            answer = ""
            if incoming.message_type in {"image", "document"}:
                media_payload = incoming.raw_payload.get(incoming.message_type, {})
                received_label = "sua foto" if incoming.message_type == "image" else (
                    "seu PDF" if media_payload.get("mime_type") == "application/pdf" else "seu arquivo"
                )
                answer = (
                    f"Recebi {received_label}. O pagamento ainda não está confirmado. O arquivo "
                    "está em processamento e foi encaminhado ao setor responsável para validação."
                )
            if incoming.message_type == "audio" and not incoming.transcription:
                try:
                    source_media_id = incoming.source_media_id or incoming.raw_payload.get("audio", {}).get("id", "")
                    audio_data, mime_type = download_whatsapp_audio(
                        source_media_id, incoming.conversation.whatsapp_number
                    )
                    incoming.transcription = transcribe_audio(audio_data, mime_type)
                    incoming.text = incoming.transcription
                    incoming.source_media_id = source_media_id
                    incoming.save(update_fields=("transcription", "text", "source_media_id"))
                except ValueError:
                    answer = "Desculpe, não consegui compreender esse áudio. Pode enviar novamente ou escrever sua mensagem?"
            if not answer:
                recent_messages = list(
                    Message.objects.filter(conversation_id=incoming.conversation_id)
                    .exclude(pk=incoming.pk)
                    .exclude(text="")
                    .order_by("-created_at", "-pk")
                    .values_list("direction", "text")[:4]
                )
                recent_messages.reverse()
                media_context = "\n".join(
                    f"{'CLIENTE' if direction == Message.Direction.INBOUND else 'ATENDENTE'}: {text[:400]}"
                    for direction, text in recent_messages
                )
                try:
                    selected_media = select_media_for_question(
                        incoming.company_id, incoming.text, media_context,
                        business_unit_id=conversation.business_unit_id,
                    )
                except Exception:
                    logger.exception(
                        "Falha ao selecionar mídia para a mensagem %s", incoming.pk
                    )
                answer = answer_with_rag(
                    incoming.company_id,
                    incoming.text,
                    conversation_id=incoming.conversation_id,
                    current_message_id=incoming.pk,
                    selected_media=selected_media,
                )
        except Exception as exc:
            incoming.ai_error = str(exc)[:2000]
            if self.request.retries >= self.max_retries:
                incoming.ai_status = Message.AIStatus.FAILED
                incoming.save(update_fields=["ai_status", "ai_error"])
                raise
            incoming.save(update_fields=["ai_error"])
            raise self.retry(exc=exc, countdown=min(2 ** self.request.retries, 30))

        with transaction.atomic():
            incoming = Message.objects.select_for_update().get(pk=incoming.pk)
            conversation = Conversation.objects.select_for_update().select_related("whatsapp_number").get(
                pk=incoming.conversation_id
            )
            if (
                conversation.ai_paused
                or not conversation.whatsapp_number.ai_enabled
                or not conversation.whatsapp_number.is_active
                or not number_billing_available(conversation.whatsapp_number)
            ):
                incoming.ai_status = Message.AIStatus.SKIPPED
                incoming.ai_error = ""
                incoming.save(update_fields=["ai_status", "ai_error"])
                return
            outgoing, created = Message.objects.get_or_create(
                response_to=incoming,
                defaults={
                    "external_id": f"ai-pending:{incoming.pk}",
                    "company_id": incoming.company_id,
                    "conversation_id": incoming.conversation_id,
                    "direction": Message.Direction.OUTBOUND,
                    "message_type": "text",
                    "text": answer,
                    "status": Message.Status.PENDING,
                },
            )
        if not created:
            incoming.ai_status = Message.AIStatus.FAILED
            incoming.ai_error = "Resposta já reservada; envio duplicado bloqueado."
            incoming.save(update_fields=["ai_status", "ai_error"])
            return

        try:
            external_id = send_whatsapp_text(number, conversation.contact.wa_id, answer)
        except Exception as exc:
            outgoing.status = Message.Status.FAILED
            outgoing.raw_payload = {"error": str(exc)[:2000]}
            outgoing.save(update_fields=["status", "raw_payload"])
            incoming.ai_status, incoming.ai_error = Message.AIStatus.FAILED, str(exc)[:2000]
            incoming.save(update_fields=["ai_status", "ai_error"])
            raise

        outgoing.external_id, outgoing.status = external_id, Message.Status.SENT
        outgoing.sent_at = timezone.now()
        outgoing.save(update_fields=["external_id", "status", "sent_at"])
        conversation.last_message_at = outgoing.created_at
        conversation.save(update_fields=["last_message_at"])
        incoming.ai_status, incoming.ai_error = Message.AIStatus.COMPLETED, ""
        incoming.save(update_fields=["ai_status", "ai_error"])
        if selected_media:
            send_selected_media.delay(incoming.pk, selected_media.pk)
        if incoming.message_type == "audio":
            analyze_conversation.delay(incoming.conversation_id)
    finally:
        try:
            lock.release()
        except LockError:
            pass
