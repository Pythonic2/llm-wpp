import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from cryptography.fernet import Fernet
from django.test import Client
from django.utils import timezone

from saas.meta import MetaConfigurationError, access_token_for_number, register_cloud_api_number, verify_embedded_signup_assets
from saas.models import CoexistenceWebhookBatch, EmbeddedSignupAttempt, Message, WhatsAppBusinessAccount, WhatsAppNumber
from saas.tasks import process_coexistence_history


def configure(settings):
    settings.META_APP_ID = "test-app"
    settings.META_EMBEDDED_SIGNUP_CONFIG_ID = "test-config"
    settings.WHATSAPP_APP_SECRET = "test-secret"
    settings.WHATSAPP_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()


def urls(company):
    root = f"/api/v1/companies/{company.pk}/whatsapp/embedded-signup/"
    return root + "config/", root + "complete/"


def fake_assets(phone_id="123456"):
    return {"id": "123", "name": "Conta de teste"}, {"id": phone_id, "display_phone_number": "+15550123"}


@pytest.mark.django_db
@patch("saas.api.request_coexistence_sync")
@patch("saas.api.register_cloud_api_number")
@patch("saas.api.subscribe_app_to_waba")
@patch("saas.api.verify_embedded_signup_assets")
@patch("saas.api.exchange_embedded_signup_code", return_value="test-token")
def test_normal_signup_and_one_use_state(exchange, verify, subscribe, register, sync, client, tenant_data, settings):
    configure(settings)
    verify.return_value = fake_assets()
    client.force_login(tenant_data["user"])
    config_url, complete_url = urls(tenant_data["company"])
    state = client.get(config_url).json()["state"]
    payload = {"code": "test-code", "waba_id": "123", "phone_number_id": "123456", "event": "FINISH", "state": state, "standard_migration_consent": True}
    assert client.post(complete_url, {**payload, "standard_migration_consent": False}, content_type="application/json").status_code == 400
    exchange.assert_not_called()
    response = client.post(complete_url, payload, content_type="application/json")
    assert response.status_code == 201
    assert response.json()["connection_mode"] == "cloud_api"
    assert response.json()["business_account_name"] == "Conta de teste"
    assert EmbeddedSignupAttempt.objects.get(pk=state).standard_migration_consent_at
    assert "test-token" not in str(response.json())
    number = WhatsAppNumber.objects.get(phone_number_id="123456")
    assert number.company == tenant_data["company"]
    assert number.access_token_encrypted != "test-token"
    verify.assert_called_once_with("test-token", "123", "123456", False)
    register.assert_called_once_with("test-token", "123456")
    subscribe.assert_called_once()
    sync.assert_not_called()
    assert client.post(complete_url, payload, content_type="application/json").status_code == 400
    exchange.assert_called_once()


@pytest.mark.django_db
@patch("saas.api.exchange_embedded_signup_code")
def test_state_is_bound_to_user_company_and_expiry(exchange, client, tenant_data, settings):
    configure(settings)
    client.force_login(tenant_data["user"])
    config_url, complete_url = urls(tenant_data["company"])
    state = client.get(config_url).json()["state"]
    payload = {"code": "test-code", "waba_id": "123", "event": "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING", "state": state}
    client.force_login(tenant_data["other"])
    assert client.post(urls(tenant_data["company_b"])[1], payload, content_type="application/json").status_code == 400
    client.force_login(tenant_data["user"])
    EmbeddedSignupAttempt.objects.filter(pk=state).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client.post(complete_url, payload, content_type="application/json").status_code == 400
    exchange.assert_not_called()


@pytest.mark.django_db
@patch("saas.api.register_cloud_api_number")
@patch("saas.api.subscribe_app_to_waba")
@patch("saas.api.verify_embedded_signup_assets")
@patch("saas.api.exchange_embedded_signup_code", return_value="test-token")
def test_number_owned_by_another_company_is_rejected(exchange, verify, subscribe, register, client, tenant_data, settings):
    configure(settings)
    verify.return_value = fake_assets(tenant_data["number_b"].phone_number_id)
    client.force_login(tenant_data["user"])
    config_url, complete_url = urls(tenant_data["company"])
    state = client.get(config_url).json()["state"]
    response = client.post(complete_url, {
        "code": "test-code", "waba_id": "123", "event": "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING", "state": state,
    }, content_type="application/json")
    assert response.status_code == 400
    tenant_data["number_b"].refresh_from_db()
    assert tenant_data["number_b"].company_id == tenant_data["company_b"].pk
    subscribe.assert_not_called()
    register.assert_not_called()


@pytest.mark.django_db
@patch("saas.api.subscribe_app_to_waba")
@patch("saas.api.verify_embedded_signup_assets")
@patch("saas.api.exchange_embedded_signup_code", return_value="test-token")
def test_waba_claim_is_tenant_isolated(exchange, verify, subscribe, client, tenant_data, settings):
    configure(settings)
    WhatsAppBusinessAccount.objects.create(business_account_id="123", company=tenant_data["company_b"])
    verify.return_value = fake_assets()
    client.force_login(tenant_data["user"])
    config_url, complete_url = urls(tenant_data["company"])
    state = client.get(config_url).json()["state"]
    response = client.post(complete_url, {
        "code": "test-code", "waba_id": "123", "event": "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING", "state": state,
    }, content_type="application/json")
    assert response.status_code == 400
    assert WhatsAppBusinessAccount.objects.get(business_account_id="123").company_id == tenant_data["company_b"].pk
    subscribe.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("failure_stage", ["exchange", "verify"])
def test_meta_failure_does_not_expose_token(failure_stage, client, tenant_data, settings):
    configure(settings)
    client.force_login(tenant_data["user"])
    config_url, complete_url = urls(tenant_data["company"])
    state = client.get(config_url).json()["state"]
    with patch("saas.api.exchange_embedded_signup_code", return_value="private-token") as exchange, patch(
        "saas.api.verify_embedded_signup_assets", side_effect=RuntimeError("invalid-private-token"),
    ) as verify:
        if failure_stage == "exchange":
            exchange.side_effect = RuntimeError("expired-private-token")
        response = client.post(complete_url, {
            "code": "expired", "waba_id": "123", "event": "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING", "state": state,
        }, content_type="application/json")
        assert exchange.call_count == 1
        assert verify.call_count == (0 if failure_stage == "exchange" else 1)
    assert response.status_code == 400
    assert "private-token" not in str(response.json())
    assert WhatsAppNumber.objects.filter(phone_number_id="123456").count() == 0


@pytest.mark.django_db
def test_csrf_and_local_disconnect(client, tenant_data, settings):
    configure(settings)
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(tenant_data["user"])
    config_url, complete_url = urls(tenant_data["company"])
    assert strict.get(config_url).status_code == 200
    assert strict.post(complete_url, {}, content_type="application/json").status_code == 403
    number = tenant_data["number"]
    number.connection_mode = "coexistence"
    number.access_token_encrypted = "encrypted-test"
    number.save()
    disconnect_url = f"/api/v1/companies/{tenant_data['company'].pk}/whatsapp-numbers/{number.pk}/disconnect/"
    assert client.post(disconnect_url).status_code in {401, 403}
    client.force_login(tenant_data["other"])
    assert client.post(disconnect_url).status_code == 404
    client.force_login(tenant_data["user"])
    assert client.post(disconnect_url).status_code == 200
    number.refresh_from_db()
    assert number.connection_state == "disconnected" and not number.is_active and not number.access_token_encrypted
    with pytest.raises(MetaConfigurationError):
        access_token_for_number(number)
    assert client.patch(f"/api/v1/companies/{tenant_data['company'].pk}/whatsapp-numbers/{number.pk}/", {"is_active": True}, content_type="application/json").status_code == 400


def test_meta_asset_validation_requires_unique_eligible_number(settings):
    settings.WHATSAPP_API_VERSION = "v26.0"
    responses = [
        {"id": "123", "name": "Test"},
        {"data": [{"id": "1"}, {"id": "2"}]},
        {"id": "1", "display_phone_number": "+15550001", "is_on_biz_app": True, "platform_type": "CLOUD_API"},
        {"id": "2", "display_phone_number": "+15550002", "is_on_biz_app": True, "platform_type": "CLOUD_API"},
    ]
    with patch("saas.meta.httpx.get", side_effect=[Mock(json=Mock(return_value=data)) for data in responses]):
        with pytest.raises(ValueError, match="número único"):
            verify_embedded_signup_assets("token", "123", "", True)
    with pytest.raises(ValueError, match="Identificadores"):
        verify_embedded_signup_assets("token", "../other", "", True)


def test_meta_asset_validation_rejects_normal_flow_for_app_number(settings):
    settings.WHATSAPP_API_VERSION = "v26.0"
    responses = [
        {"id": "123", "name": "Test"},
        {"data": [{"id": "1"}]},
        {"id": "1", "display_phone_number": "+15550001", "is_on_biz_app": True, "platform_type": "CLOUD_API"},
    ]
    with patch("saas.meta.httpx.get", side_effect=[Mock(json=Mock(return_value=data)) for data in responses]):
        with pytest.raises(ValueError, match="elegível"):
            verify_embedded_signup_assets("token", "123", "1", False)


def test_normal_registration_uses_stable_private_pin(settings):
    settings.WHATSAPP_API_VERSION = "v26.0"
    settings.WHATSAPP_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()
    with patch("saas.meta.httpx.post", return_value=Mock(json=Mock(return_value={"success": True}))) as post:
        register_cloud_api_number("test-token", "123456")
        register_cloud_api_number("test-token", "123456")
    first, second = post.call_args_list
    assert first.kwargs["json"]["pin"] == second.kwargs["json"]["pin"]
    assert len(first.kwargs["json"]["pin"]) == 6 and first.kwargs["json"]["pin"].isdigit()


@pytest.mark.django_db
@patch("saas.api.request_coexistence_sync")
@patch("saas.api.subscribe_app_to_waba")
@patch("saas.api.verify_embedded_signup_assets")
@patch("saas.api.exchange_embedded_signup_code", return_value="replacement-token")
def test_reconnection_keeps_same_tenant_and_replaces_credential(exchange, verify, subscribe, sync, client, tenant_data, settings):
    configure(settings)
    number = tenant_data["number"]
    number.connection_mode = "coexistence"
    number.business_account_id = "123"
    number.connection_state = "disconnected"
    number.is_active = False
    number.save()
    verify.return_value = fake_assets(number.phone_number_id)
    sync.side_effect = ["contacts-2", "history-2"]
    client.force_login(tenant_data["user"])
    config_url, complete_url = urls(tenant_data["company"])
    state = client.get(config_url).json()["state"]
    response = client.post(complete_url, {
        "code": "new-code", "waba_id": "123", "event": "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING", "state": state,
    }, content_type="application/json")
    assert response.status_code == 201
    number.refresh_from_db()
    assert number.is_active and number.connection_state == "syncing"
    assert access_token_for_number(number) == "replacement-token"
    assert WhatsAppNumber.objects.filter(phone_number_id=number.phone_number_id).count() == 1
    assert number.contacts_sync_request_id == "contacts-2"


def signed_post(client, settings, payload):
    settings.WHATSAPP_APP_SECRET = "test-secret"
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    return client.post("/api/webhooks/whatsapp/", data=body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature)


@pytest.mark.django_db
@patch("saas.webhooks.analyze_conversation.delay")
@patch("saas.webhooks.answer_inbound_message.delay")
def test_app_echo_is_idempotent_and_pauses_ai(answer, analyze, client, tenant_data, settings):
    number = tenant_data["number"]
    number.connection_mode = "coexistence"
    number.business_account_id = "123"
    number.save()
    payload = {"entry": [{"id": "123", "changes": [{"field": "smb_message_echoes", "value": {
        "metadata": {"phone_number_id": number.phone_number_id},
        "message_echoes": [{"id": "wamid.echo", "to": "5511777777777", "type": "text", "text": {"body": "Resposta manual"}}],
    }}]}]}
    assert signed_post(client, settings, payload).status_code == 200
    assert signed_post(client, settings, payload).status_code == 200
    message = Message.objects.get(external_id="wamid.echo")
    assert message.direction == Message.Direction.OUTBOUND
    assert message.conversation.ai_paused
    assert Message.objects.filter(external_id="wamid.echo").count() == 1
    answer.assert_not_called()
    analyze.assert_not_called()


@pytest.mark.django_db
def test_partner_removed_webhook_deactivates_coexistence(client, tenant_data, settings):
    number = tenant_data["number"]
    number.connection_mode = "coexistence"
    number.business_account_id = "123"
    number.access_token_encrypted = "encrypted-test"
    number.save()
    payload = {"entry": [{"id": "123", "changes": [{"field": "account_update", "value": {"event": "PARTNER_REMOVED"}}]}]}
    assert signed_post(client, settings, payload).status_code == 200
    number.refresh_from_db()
    assert number.connection_state == "disconnected" and not number.is_active and not number.access_token_encrypted


@pytest.mark.django_db
@patch("saas.webhooks.analyze_conversation.delay")
@patch("saas.webhooks.answer_inbound_message.delay")
def test_business_scoped_id_without_phone_is_stored_without_automatic_reply(answer, analyze, client, tenant_data, settings):
    number = tenant_data["number"]
    number.connection_mode = "coexistence"
    number.business_account_id = "123"
    number.save()
    payload = {"entry": [{"id": "123", "changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": number.phone_number_id},
        "messages": [{"id": "wamid.bsuid", "from_user_id": "user-123", "type": "text", "text": {"body": "Olá"}}],
    }}]}]}
    assert signed_post(client, settings, payload).status_code == 200
    message = Message.objects.get(external_id="wamid.bsuid")
    assert message.conversation.contact.wa_id == "bsuid:user-123"
    assert message.ai_status == Message.AIStatus.SKIPPED
    answer.assert_not_called()
    analyze.assert_not_called()


@pytest.mark.django_db
@patch("saas.webhooks.process_coexistence_history.delay")
@patch("saas.webhooks.answer_inbound_message.delay")
def test_history_batch_is_durable_idempotent_and_never_queues_ai(answer, delay, client, tenant_data, settings, django_capture_on_commit_callbacks):
    number = tenant_data["number"]
    number.connection_mode = "coexistence"
    number.business_account_id = "123"
    number.save()
    payload = {"entry": [{"id": "123", "changes": [{"field": "history", "value": {
        "metadata": {"phone_number_id": number.phone_number_id},
        "history": [{"metadata": {"phase": 0, "progress": 100}, "threads": [{
            "context": {"wa_id": "5511777777777"},
            "messages": [{"id": "wamid.old", "from": "5511777777777", "type": "text", "text": {"body": "Histórico"}}],
        }]}],
    }}]}]}
    with django_capture_on_commit_callbacks(execute=True):
        assert signed_post(client, settings, payload).status_code == 200
        assert signed_post(client, settings, payload).status_code == 200
    assert CoexistenceWebhookBatch.objects.count() == 1
    assert delay.call_count == 2
    process_coexistence_history(CoexistenceWebhookBatch.objects.first().pk)
    process_coexistence_history(CoexistenceWebhookBatch.objects.first().pk)
    assert Message.objects.filter(external_id="wamid.old").count() == 1
    assert CoexistenceWebhookBatch.objects.first().payload == {}
    assert Message.objects.get(external_id="wamid.old").ai_status == Message.AIStatus.SKIPPED
    number.refresh_from_db()
    assert number.connection_state == "connected" and number.last_synced_at
    answer.assert_not_called()
