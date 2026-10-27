import hashlib, hmac, json
from unittest.mock import patch
import pytest
from django.urls import reverse
from saas.models import Message

pytestmark = pytest.mark.django_db

def signature(secret, body): return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
def payload():
    return {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "meta-test-id"}, "contacts": [{"wa_id": "5511777777777", "profile": {"name": "Maria"}}], "messages": [{"id": "wamid.123", "from": "5511777777777", "type": "text", "text": {"body": "Olá"}}]}}]}]}

def test_webhook_verification(client, settings):
    settings.WHATSAPP_VERIFY_TOKEN = "verify-me"
    response = client.get(reverse("whatsapp-webhook"), {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert response.status_code == 200 and response.content == b"42"

def test_webhook_rejects_bad_signature(client, settings):
    settings.WHATSAPP_APP_SECRET = "secret"
    assert client.post(reverse("whatsapp-webhook"), data=b"{}", content_type="application/json", HTTP_X_HUB_SIGNATURE_256="bad").status_code == 403

@patch("saas.webhooks.analyze_conversation.delay")
@patch("saas.webhooks.answer_inbound_message.delay")
def test_webhook_is_idempotent(delay, analyze, client, settings, tenant_data, django_capture_on_commit_callbacks):
    settings.WHATSAPP_APP_SECRET = "secret"
    body = json.dumps(payload()).encode()
    headers = {"HTTP_X_HUB_SIGNATURE_256": signature("secret", body)}
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(reverse("whatsapp-webhook"), data=body, content_type="application/json", **headers).status_code == 200
        assert client.post(reverse("whatsapp-webhook"), data=body, content_type="application/json", **headers).status_code == 200
    assert Message.objects.filter(external_id="wamid.123").count() == 1
    delay.assert_called_once()
    analyze.assert_called_once()
