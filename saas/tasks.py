import hashlib
import json
import logging
import mimetypes
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from redis.exceptions import LockError

from .models import Company, Conversation, KnowledgeChunk, KnowledgeDocument, MediaAsset, Message
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


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def store_inbound_attachment(message_id):
    message = Message.objects.get(pk=message_id)
    if message.attachment or not message.source_media_id or message.message_type not in {"image", "document", "audio"}:
        return
    if message.attachment_error == "processing":
        return
    Message.objects.filter(pk=message.pk).update(attachment_error="processing")
    try:
        content, mime_type = download_whatsapp_attachment(message.source_media_id)
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
        if should_notify_hot_lead:
            transaction.on_commit(lambda: notify_hot_lead.delay(conversation.pk))


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
                    audio_data, mime_type = download_whatsapp_audio(source_media_id)
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
                        incoming.company_id, incoming.text, media_context
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
        if selected_media:
            send_selected_media.delay(incoming.pk, selected_media.pk)
        if incoming.message_type == "audio":
            analyze_conversation.delay(incoming.conversation_id)
    finally:
        try:
            lock.release()
        except LockError:
            pass
