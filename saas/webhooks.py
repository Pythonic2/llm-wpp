import hashlib
import hmac
import json

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .coexistence import process_account_update, process_app_contacts, process_app_echoes
from .message_time import whatsapp_timestamp
from .models import CoexistenceWebhookBatch, Contact, Conversation, Message, WhatsAppNumber
from .tasks import analyze_conversation, answer_inbound_message, process_coexistence_history, store_inbound_attachment


def _valid_signature(request):
    if not settings.WHATSAPP_APP_SECRET: return False
    received = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(settings.WHATSAPP_APP_SECRET.encode(), request.body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(received, expected)


@csrf_exempt
def whatsapp_webhook(request):
    if request.method == "GET":
        if settings.WHATSAPP_VERIFY_TOKEN and request.GET.get("hub.mode") == "subscribe" and hmac.compare_digest(request.GET.get("hub.verify_token", ""), settings.WHATSAPP_VERIFY_TOKEN):
            return HttpResponse(request.GET.get("hub.challenge", ""))
        return HttpResponse(status=403)
    if request.method != "POST": return HttpResponse(status=405)
    if not _valid_signature(request): return HttpResponse(status=403)
    try: payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError): return JsonResponse({"error": "invalid json"}, status=400)
    if not isinstance(payload, dict) or not isinstance(payload.get("entry", []), list):
        return JsonResponse({"error": "invalid payload"}, status=400)
    for entry in payload.get("entry", []):
        if not isinstance(entry, dict): continue
        for change in entry.get("changes", []):
            if not isinstance(change, dict): continue
            value = change.get("value", {})
            if not isinstance(value, dict): continue
            metadata = value.get("metadata", {})
            if not isinstance(metadata, dict): continue
            phone_id = metadata.get("phone_number_id")
            if change.get("field") == "account_update" and not phone_id:
                for related in WhatsAppNumber.objects.filter(business_account_id=str(entry.get("id", ""))):
                    process_account_update(related, value)
                continue
            number = WhatsAppNumber.objects.filter(phone_number_id=phone_id, is_active=True).first() if phone_id else None
            if not number: continue
            if entry.get("id") and number.business_account_id and str(entry["id"]) != number.business_account_id:
                continue
            if change.get("field") == "account_update":
                process_account_update(number, value)
                continue
            if change.get("field") == "smb_message_echoes":
                process_app_echoes(number, value)
                continue
            if change.get("field") == "smb_app_state_sync":
                process_app_contacts(number, value)
                continue
            if change.get("field") == "history":
                if number.connection_mode == "coexistence":
                    fingerprint = hashlib.sha256(json.dumps(
                        {"number_id": number.pk, "value": value}, sort_keys=True, separators=(",", ":"),
                    ).encode()).hexdigest()
                    batch, _ = CoexistenceWebhookBatch.objects.get_or_create(
                        fingerprint=fingerprint, defaults={"number": number, "payload": value},
                    )
                    if batch.processed_at is None:
                        transaction.on_commit(lambda pk=batch.pk: process_coexistence_history.delay(pk))
                continue
            if change.get("field") not in (None, "messages"):
                continue
            for status_item in value.get("statuses", []):
                message_status = status_item.get("status")
                if message_status in {Message.Status.SENT, Message.Status.DELIVERED, Message.Status.READ, Message.Status.FAILED}:
                    Message.objects.filter(
                        company=number.company, external_id=status_item.get("id", "")
                    ).update(status=message_status, raw_payload=status_item)
            profiles = {
                str(item.get("wa_id") or (f"bsuid:{item['user_id']}" if item.get("user_id") else "")):
                item.get("profile", {}).get("name", "")
                for item in value.get("contacts", []) if isinstance(item, dict)
            }
            for item in value.get("messages", []):
                if not isinstance(item, dict): continue
                external_id = item.get("id")
                wa_id = item.get("from") or (f"bsuid:{item['from_user_id']}" if item.get("from_user_id") else "")
                if not external_id or len(str(external_id)) > 128 or not wa_id or len(str(wa_id)) > 64: continue
                with transaction.atomic():
                    contact, _ = Contact.objects.update_or_create(company=number.company, wa_id=wa_id, defaults={"name": profiles.get(str(wa_id), ""), "phone": item.get("from", "")})
                    conversation, _ = Conversation.objects.get_or_create(company=number.company, whatsapp_number=number, contact=contact)
                    message_type = item.get("type", "unknown")
                    ai_eligible = not str(wa_id).startswith("bsuid:")
                    ai_status = Message.AIStatus.QUEUED if ai_eligible and message_type in {"text", "audio", "image", "document"} else Message.AIStatus.SKIPPED
                    media_payload = item.get(message_type, {}) if message_type in {"audio", "image", "document", "video"} else {}
                    text = item.get("text", {}).get("body", "") or media_payload.get("caption", "")
                    message_time = whatsapp_timestamp(item.get("timestamp")) or timezone.now()
                    message, created = Message.objects.get_or_create(external_id=external_id, defaults={"company": number.company, "conversation": conversation, "direction": Message.Direction.INBOUND, "message_type": message_type, "text": text, "source_media_id": media_payload.get("id", ""), "raw_payload": item, "ai_status": ai_status, "sent_at": message_time})
                    if created:
                        fields = []
                        if conversation.last_message_at is None or message_time > conversation.last_message_at:
                            conversation.last_message_at = message_time
                            fields.append("last_message_at")
                        if ai_eligible and conversation.first_eligible_inbound_at is None:
                            conversation.first_eligible_inbound_at = message_time
                            fields.append("first_eligible_inbound_at")
                        if fields:
                            conversation.save(update_fields=fields)
                        if ai_eligible:
                            transaction.on_commit(lambda pk=message.pk: answer_inbound_message.delay(pk))
                        if message_type in {"document", "audio"}:
                            transaction.on_commit(lambda pk=message.pk: store_inbound_attachment.delay(pk))
                        if ai_eligible and message_type == "text":
                            transaction.on_commit(lambda pk=conversation.pk: analyze_conversation.delay(pk))
    return JsonResponse({"status": "ok"})
