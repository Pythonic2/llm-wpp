import hashlib
import hmac
import json

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import Contact, Conversation, Message, WhatsAppNumber
from .tasks import analyze_conversation, answer_inbound_message


def _valid_signature(request):
    if not settings.WHATSAPP_APP_SECRET: return False
    received = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(settings.WHATSAPP_APP_SECRET.encode(), request.body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(received, expected)


@csrf_exempt
def whatsapp_webhook(request):
    if request.method == "GET":
        if request.GET.get("hub.mode") == "subscribe" and hmac.compare_digest(request.GET.get("hub.verify_token", ""), settings.WHATSAPP_VERIFY_TOKEN):
            return HttpResponse(request.GET.get("hub.challenge", ""))
        return HttpResponse(status=403)
    if request.method != "POST": return HttpResponse(status=405)
    if not _valid_signature(request): return HttpResponse(status=403)
    try: payload = json.loads(request.body)
    except json.JSONDecodeError: return JsonResponse({"error": "invalid json"}, status=400)
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            number = WhatsAppNumber.objects.filter(phone_number_id=value.get("metadata", {}).get("phone_number_id"), is_active=True).first()
            if not number: continue
            profiles = {item.get("wa_id"): item.get("profile", {}).get("name", "") for item in value.get("contacts", [])}
            for item in value.get("messages", []):
                external_id, wa_id = item.get("id"), item.get("from")
                if not external_id or not wa_id: continue
                with transaction.atomic():
                    contact, _ = Contact.objects.update_or_create(company=number.company, wa_id=wa_id, defaults={"name": profiles.get(wa_id, ""), "phone": wa_id})
                    conversation, _ = Conversation.objects.get_or_create(company=number.company, whatsapp_number=number, contact=contact)
                    message_type = item.get("type", "unknown")
                    ai_status = Message.AIStatus.QUEUED if message_type == "text" else Message.AIStatus.SKIPPED
                    message, created = Message.objects.get_or_create(external_id=external_id, defaults={"company": number.company, "conversation": conversation, "direction": Message.Direction.INBOUND, "message_type": message_type, "text": item.get("text", {}).get("body", ""), "raw_payload": item, "ai_status": ai_status})
                    if created:
                        conversation.last_message_at = timezone.now()
                        conversation.save(update_fields=["last_message_at"])
                        transaction.on_commit(lambda pk=message.pk: answer_inbound_message.delay(pk))
                        transaction.on_commit(lambda pk=conversation.pk: analyze_conversation.delay(pk))
    return JsonResponse({"status": "ok"})
