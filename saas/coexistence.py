from datetime import datetime, timezone as dt_timezone

from django.db import transaction
from django.utils import timezone

from .models import Contact, Conversation, Message, WhatsAppNumber


def _contact(number, phone="", user_id="", name=""):
    key = str(phone or (f"bsuid:{user_id}" if user_id else ""))
    if not key or len(key) > 64:
        return None, None
    contact, _ = Contact.objects.get_or_create(
        company=number.company, wa_id=key,
        defaults={"phone": str(phone)[:32], "name": str(name)[:160]},
    )
    conversation, _ = Conversation.objects.get_or_create(
        company=number.company, whatsapp_number=number, contact=contact,
    )
    return contact, conversation


def _timestamp(value):
    try:
        return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return timezone.now()


def process_app_echoes(number, value):
    if number.connection_mode != "coexistence":
        return
    profiles = {
        str(item.get("wa_id") or item.get("user_id")): item.get("profile", {}).get("name", "")
        for item in value.get("contacts", []) if isinstance(item, dict)
    }
    for item in value.get("message_echoes", []):
        if not isinstance(item, dict):
            continue
        external_id = str(item.get("id") or "")
        phone = str(item.get("to") or "")
        user_id = str(item.get("to_user_id") or "")
        if not external_id or len(external_id) > 128:
            continue
        with transaction.atomic():
            _, conversation = _contact(number, phone, user_id, profiles.get(phone or user_id, ""))
            if conversation is None:
                continue
            message_type = str(item.get("type") or "unknown")[:32]
            content = item.get(message_type, {})
            text = content.get("body", "") or content.get("caption", "") if isinstance(content, dict) else ""
            message, created = Message.objects.get_or_create(
                external_id=external_id,
                defaults={"company": number.company, "conversation": conversation,
                          "direction": Message.Direction.OUTBOUND, "message_type": message_type,
                          "text": text, "status": Message.Status.SENT,
                          "raw_payload": item, "ai_status": Message.AIStatus.NOT_APPLICABLE},
            )
            if created:
                message_time = _timestamp(item.get("timestamp"))
                if conversation.last_message_at is None or message_time > conversation.last_message_at:
                    conversation.last_message_at = message_time
                if number.pause_ai_on_app_echo:
                    conversation.ai_paused = True
                    conversation.ai_paused_at = timezone.now()
                    conversation.save(update_fields=["last_message_at", "ai_paused", "ai_paused_at"])
                else:
                    conversation.save(update_fields=["last_message_at"])


def process_app_contacts(number, value):
    if number.connection_mode != "coexistence":
        return
    for item in value.get("state_sync", []):
        if not isinstance(item, dict) or item.get("type") != "contact" or item.get("action") == "remove":
            continue
        data = item.get("contact", {})
        if not isinstance(data, dict):
            continue
        phone = str(data.get("phone_number") or "")
        user_id = str(data.get("user_id") or "")
        name = str(data.get("full_name") or data.get("first_name") or "")[:160]
        contact, _ = _contact(number, phone, user_id, name)
        if contact and name and contact.name != name:
            contact.name = name
            contact.save(update_fields=["name"])
    WhatsAppNumber.objects.filter(pk=number.pk).update(last_synced_at=timezone.now())


def process_app_history(number, value):
    if number.connection_mode != "coexistence":
        return
    business_phone = "".join(c for c in number.display_phone_number if c.isdigit())
    for chunk in value.get("history", []):
        if not isinstance(chunk, dict):
            continue
        if chunk.get("errors"):
            WhatsAppNumber.objects.filter(pk=number.pk).update(
                connection_state="history_declined", connection_error="", last_synced_at=timezone.now(),
            )
            continue
        for thread in chunk.get("threads", []):
            if not isinstance(thread, dict):
                continue
            context = thread.get("context", {})
            if not isinstance(context, dict):
                context = {}
            phone = str(context.get("wa_id") or thread.get("id") or "")
            user_id = str(context.get("user_id") or "")
            _, conversation = _contact(number, phone, user_id)
            if conversation is None:
                continue
            for item in thread.get("messages", []):
                if not isinstance(item, dict):
                    continue
                external_id = str(item.get("id") or "")
                if not external_id or len(external_id) > 128:
                    continue
                message_type = str(item.get("type") or "unknown")[:32]
                content = item.get(message_type, {})
                text = content.get("body", "") or content.get("caption", "") if isinstance(content, dict) else ""
                outbound = str(item.get("from") or "") == business_phone or bool(item.get("to"))
                _, created = Message.objects.get_or_create(
                    external_id=external_id,
                    defaults={"company": number.company, "conversation": conversation,
                              "direction": Message.Direction.OUTBOUND if outbound else Message.Direction.INBOUND,
                              "message_type": message_type, "text": text,
                              "status": Message.Status.SENT if outbound else Message.Status.RECEIVED,
                              "raw_payload": item, "ai_status": Message.AIStatus.SKIPPED},
                )
                if created:
                    message_time = _timestamp(item.get("timestamp"))
                    if conversation.last_message_at is None or message_time > conversation.last_message_at:
                        conversation.last_message_at = message_time
                        conversation.save(update_fields=["last_message_at"])
        if chunk.get("metadata", {}).get("progress") == 100:
            WhatsAppNumber.objects.filter(pk=number.pk).update(connection_state="connected", connection_error="")
    WhatsAppNumber.objects.filter(pk=number.pk).update(last_synced_at=timezone.now())


def process_account_update(number, value):
    if value.get("event") == "PARTNER_REMOVED" and number.connection_mode == "coexistence":
        WhatsAppNumber.objects.filter(pk=number.pk).update(
            is_active=False, ai_enabled=False, access_token_encrypted="", connection_state="disconnected",
        )
