from celery import shared_task
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from redis.exceptions import LockError

from .models import Conversation, KnowledgeChunk, KnowledgeDocument, Message
from .services import (
    answer_with_rag,
    analyze_sales_conversation,
    conversation_ai_lock,
    embed_texts,
    extract_text,
    send_whatsapp_text,
    split_text,
)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def analyze_conversation(conversation_id):
    analysis = analyze_sales_conversation(conversation_id)
    with transaction.atomic():
        conversation = Conversation.objects.select_for_update().select_related("company").get(pk=conversation_id)
        became_hot = (
            conversation.lead_temperature != Conversation.LeadTemperature.HOT
            and analysis["temperature"] == Conversation.LeadTemperature.HOT
        )
        conversation.lead_temperature = analysis["temperature"]
        conversation.purchase_intent_score = analysis["score"]
        conversation.analysis_summary = analysis["summary"]
        conversation.buying_signals = analysis["buying_signals"]
        conversation.objections = analysis["objections"]
        conversation.recommended_action = analysis["recommended_action"]
        conversation.analyzed_at = timezone.now()
        if analysis["temperature"] != Conversation.LeadTemperature.HOT:
            conversation.hot_lead_notified_at = None
        elif (
            became_hot
            and conversation.company.hot_lead_notifications_enabled
            and conversation.company.hot_lead_notification_phone
        ):
            conversation.hot_lead_notified_at = timezone.now()
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
            ]
        )
        if became_hot and conversation.hot_lead_notified_at:
            transaction.on_commit(lambda: notify_hot_lead.delay(conversation.pk))


def _hot_lead_notification_text(conversation):
    contact = conversation.contact
    lines = [
        "🔥 *Novo lead quente*",
        "",
        f"Cliente: {contact.name or 'Não informado'}",
        f"WhatsApp: {contact.phone or contact.wa_id}",
        f"Intenção de compra: {conversation.purchase_intent_score}/100",
        f"Resumo: {conversation.analysis_summary or 'Sem resumo disponível.'}",
    ]
    if conversation.buying_signals:
        lines.append("Sinais de compra: " + "; ".join(conversation.buying_signals[:3]))
    if conversation.objections:
        lines.append("Objeções: " + "; ".join(conversation.objections[:3]))
    if conversation.recommended_action:
        lines.append(f"Próxima ação: {conversation.recommended_action}")
    return "\n".join(lines)[:4000]


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
    send_whatsapp_text(
        conversation.whatsapp_number,
        company.hot_lead_notification_phone,
        _hot_lead_notification_text(conversation),
    )


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


@shared_task(bind=True, max_retries=120)
def answer_inbound_message(self, message_id):
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
        if conversation.ai_paused or not number.ai_enabled or not number.is_active or incoming.message_type != "text":
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
        try:
            answer = answer_with_rag(incoming.company_id, incoming.text)
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
        outgoing.save(update_fields=["external_id", "status"])
        conversation.last_message_at = outgoing.created_at
        conversation.save(update_fields=["last_message_at"])
        incoming.ai_status, incoming.ai_error = Message.AIStatus.COMPLETED, ""
        incoming.save(update_fields=["ai_status", "ai_error"])
    finally:
        try:
            lock.release()
        except LockError:
            pass
