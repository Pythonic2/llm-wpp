from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from saas.follow_up import eligibility
from saas.models import Conversation, FollowUpAttempt, Message
from saas.tasks import send_warm_follow_up

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 11, 16, 15, 0, tzinfo=dt_timezone.utc)  # noon in Sao Paulo


def prepare_warm(tenant_data, *, hours=20, consent=True):
    conversation = tenant_data["conversation"]
    company = tenant_data["company"]
    contact = tenant_data["contact"]
    company.warm_follow_up_enabled = True
    company.save(update_fields=["warm_follow_up_enabled"])
    if consent:
        contact.follow_up_consent_at = NOW - timedelta(days=1)
        contact.follow_up_consent_source = "Cliente autorizou retorno no formulário"
        contact.save(update_fields=["follow_up_consent_at", "follow_up_consent_source"])
    conversation.lead_temperature = Conversation.LeadTemperature.WARM
    conversation.analysis_summary = "Interesse em serviço"
    conversation.last_message_at = NOW - timedelta(hours=hours)
    conversation.save(update_fields=["lead_temperature", "analysis_summary", "last_message_at"])
    incoming = Message.objects.create(
        company=company, conversation=conversation, external_id=f"wamid.warm.{hours}.{consent}",
        direction=Message.Direction.INBOUND, text="Quero conhecer o serviço",
        sent_at=NOW - timedelta(hours=hours),
    )
    return conversation, incoming


@patch("saas.tasks.timezone.now", return_value=NOW)
@patch("saas.tasks.send_whatsapp_text", return_value="wamid.followup")
@patch("saas.follow_up.generate_follow_up_text", return_value="Olá! Ainda posso ajudar com o serviço? Nossa equipe também está à disposição.")
def test_sends_once_and_reports_distinct_contact(generate, send, clock, tenant_data):
    conversation, incoming = prepare_warm(tenant_data)
    assert send_warm_follow_up(conversation.pk) == "sent"
    assert send_warm_follow_up(conversation.pk) == "already_attempted"
    send.assert_called_once()
    attempt = FollowUpAttempt.objects.get(conversation=conversation, trigger_message=incoming)
    assert attempt.status == FollowUpAttempt.Status.SENT
    assert attempt.outbound_message.external_id == "wamid.followup"
    client = APIClient()
    client.force_authenticate(user=tenant_data["user"])
    result = client.get(f"/api/v1/companies/{tenant_data['company'].pk}/dashboard/").data
    assert result["warm_follow_up_contacts_today"] == 1
    assert result["warm_follow_up_attempts"][0]["conversation_id"] == conversation.pk
    client.force_authenticate(user=tenant_data["other"])
    assert client.get(f"/api/v1/companies/{tenant_data['company'].pk}/dashboard/").status_code == 404


@patch("saas.tasks.timezone.now", return_value=NOW)
@patch("saas.tasks.send_whatsapp_text")
def test_without_consent_never_sends(send, clock, tenant_data):
    conversation, _ = prepare_warm(tenant_data, consent=False)
    assert send_warm_follow_up(conversation.pk) == "no_consent"
    assert not FollowUpAttempt.objects.exists()
    send.assert_not_called()


@patch("saas.tasks.send_whatsapp_text")
def test_outside_window_and_business_hours_never_sends(send, tenant_data):
    conversation, _ = prepare_warm(tenant_data, hours=24)
    with patch("saas.tasks.timezone.now", return_value=NOW):
        assert send_warm_follow_up(conversation.pk) == "outside_customer_window"
    with patch("saas.tasks.timezone.now", return_value=NOW.replace(hour=23)):
        assert send_warm_follow_up(conversation.pk) == "outside_business_hours"
    send.assert_not_called()


@patch("saas.tasks.timezone.now", return_value=NOW)
@patch("saas.tasks.send_whatsapp_text")
def test_new_reply_during_generation_cancels_send(send, clock, tenant_data):
    conversation, _ = prepare_warm(tenant_data)

    def generate(_conversation):
        Message.objects.create(company=conversation.company, conversation=conversation,
                               external_id="wamid.new-reply", direction=Message.Direction.INBOUND,
                               text="Voltei", sent_at=NOW - timedelta(minutes=1))
        return "Posso ajudar?"

    with patch("saas.follow_up.generate_follow_up_text", side_effect=generate):
        assert send_warm_follow_up(conversation.pk) == "outside_customer_window"
    assert FollowUpAttempt.objects.get(conversation=conversation).status == FollowUpAttempt.Status.SKIPPED
    send.assert_not_called()


def test_consent_endpoint_requires_admin_and_source(tenant_data):
    from django.contrib.auth import get_user_model
    from saas.models import Membership

    client = APIClient()
    company = tenant_data["company"]
    contact = tenant_data["contact"]
    url = f"/api/v1/companies/{company.pk}/contacts/{contact.pk}/follow-up-consent/"
    client.force_authenticate(user=tenant_data["other"])
    assert client.post(url, {"consent": True, "source": "Autorização verbal"}, format="json").status_code == 404
    agent = get_user_model().objects.create_user("warm-agent", password="test")
    Membership.objects.create(company=company, user=agent, role=Membership.Role.AGENT)
    client.force_authenticate(user=agent)
    assert client.post(url, {"consent": True, "source": "Autorização verbal"}, format="json").status_code == 403
    client.force_authenticate(user=tenant_data["user"])
    assert client.post(url, {"consent": True}, format="json").status_code == 400
    result = client.post(url, {"consent": True, "source": "Cliente autorizou no formulário"}, format="json")
    assert result.status_code == 200
    assert result.data["follow_up_consent_at"]
    result = client.post(url, {"consent": False}, format="json")
    assert result.status_code == 200
    assert result.data["follow_up_opted_out_at"]
    assert result.data["follow_up_consent_at"] is None


@patch("saas.webhooks.answer_inbound_message.delay")
def test_stop_message_revokes_consent_without_ai_reply(answer, client, settings, tenant_data, django_capture_on_commit_callbacks):
    import hashlib
    import hmac
    import json

    contact = tenant_data["contact"]
    contact.follow_up_consent_at = NOW - timedelta(days=1)
    contact.follow_up_consent_source = "Formulário de cadastro"
    contact.save(update_fields=["follow_up_consent_at", "follow_up_consent_source"])
    settings.WHATSAPP_APP_SECRET = "test-secret"
    body = json.dumps({"entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": "meta-test-id"},
        "messages": [{"id": "wamid.stop", "from": contact.wa_id, "type": "text", "text": {"body": "PARAR"}}],
    }}]}]}).encode()
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post("/api/webhooks/whatsapp/", body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature)
    assert response.status_code == 200
    contact.refresh_from_db()
    assert contact.follow_up_consent_at is None
    assert contact.follow_up_opted_out_at is not None
    assert Message.objects.get(external_id="wamid.stop").ai_status == Message.AIStatus.SKIPPED
    answer.assert_not_called()


@patch("saas.tasks.timezone.now", return_value=NOW)
@patch("saas.tasks.send_whatsapp_text", return_value="wamid.followup.reply-test")
@patch("saas.follow_up.generate_follow_up_text", return_value="Olá! Posso ajudar com sua dúvida? Nossa equipe também pode conversar.")
def test_reply_after_follow_up_updates_result(generate, send, clock, client, settings, tenant_data, django_capture_on_commit_callbacks):
    import hashlib
    import hmac
    import json

    conversation, _ = prepare_warm(tenant_data)
    assert send_warm_follow_up(conversation.pk) == "sent"
    settings.WHATSAPP_APP_SECRET = "test-secret"
    body = json.dumps({"entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": "meta-test-id"},
        "messages": [{"id": "wamid.reply", "from": conversation.contact.wa_id, "type": "text",
                      "timestamp": str(int((NOW + timedelta(minutes=2)).timestamp())), "text": {"body": "Sim, gostaria de saber mais"}}],
    }}]}]}).encode()
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    with patch("saas.webhooks.answer_inbound_message.delay"), patch("saas.webhooks.analyze_conversation.delay"):
        with django_capture_on_commit_callbacks(execute=True):
            assert client.post("/api/webhooks/whatsapp/", body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature).status_code == 200
    assert FollowUpAttempt.objects.get(conversation=conversation).responded_at == NOW + timedelta(minutes=2)


@patch("saas.tasks.timezone.now", return_value=NOW)
@patch("saas.tasks.send_whatsapp_text")
def test_daily_limit_blocks_more_submissions(send, clock, tenant_data):
    conversation, latest = prepare_warm(tenant_data)
    for index in range(20):
        previous = Message.objects.create(
            company=conversation.company, conversation=conversation,
            external_id=f"wamid.older.{index}", direction=Message.Direction.INBOUND,
            sent_at=NOW - timedelta(days=1, minutes=index),
        )
        FollowUpAttempt.objects.create(company=conversation.company, conversation=conversation,
                                       trigger_message=previous, status=FollowUpAttempt.Status.SENDING)
    assert send_warm_follow_up(conversation.pk) == "daily_limit"
    assert not FollowUpAttempt.objects.filter(trigger_message=latest).exists()
    send.assert_not_called()


@patch("saas.tasks.timezone.now", return_value=NOW)
@patch("saas.tasks.send_warm_follow_up.delay")
def test_scheduler_marks_stuck_attempt_uncertain_and_queues_eligible(delay, clock, tenant_data):
    from saas.tasks import scan_warm_follow_ups

    conversation, incoming = prepare_warm(tenant_data)
    stale = FollowUpAttempt.objects.create(
        company=conversation.company, conversation=conversation, trigger_message=incoming,
        status=FollowUpAttempt.Status.SENDING,
    )
    FollowUpAttempt.objects.filter(pk=stale.pk).update(created_at=NOW - timedelta(minutes=16))
    assert scan_warm_follow_ups() == 1
    stale.refresh_from_db()
    assert stale.status == FollowUpAttempt.Status.UNKNOWN
    delay.assert_called_once_with(conversation.pk)
    assert send_warm_follow_up(conversation.pk) == "already_attempted"
