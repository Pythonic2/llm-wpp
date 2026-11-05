from unittest.mock import Mock, patch

import pytest
from cryptography.fernet import Fernet
from django.urls import reverse

from saas.meta import access_token_for_number, encrypt_access_token
from saas.models import Message, Order, OrderStatusEvent


def test_number_token_is_encrypted_and_can_be_opened(settings):
    settings.WHATSAPP_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()
    encrypted = encrypt_access_token("customer-secret")
    assert "customer-secret" not in encrypted
    assert access_token_for_number(Mock(access_token_encrypted=encrypted)) == "customer-secret"


@pytest.mark.django_db
def test_order_list_is_tenant_isolated(client, tenant_data):
    order = Order.objects.create(
        company=tenant_data["company"], conversation=tenant_data["conversation"],
        items=[{"name": "100 doces", "quantity": "100"}],
    )
    other_order = Order.objects.create(
        company=tenant_data["company_b"],
        conversation=tenant_data["conversation"].__class__.objects.create(
            company=tenant_data["company_b"], whatsapp_number=tenant_data["number_b"],
            contact=tenant_data["contact_b"],
        ),
    )
    client.force_login(tenant_data["user"])
    response = client.get(reverse("api-orders", args=[tenant_data["company"].pk]))
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["results"]] == [str(order.pk)]
    assert str(other_order.pk) not in str(response.json())


@pytest.mark.django_db
@patch("saas.api.send_whatsapp_text", return_value="wamid.confirmed")
def test_human_approval_is_audited_and_not_repeatable(send, client, tenant_data):
    order = Order.objects.create(
        company=tenant_data["company"], conversation=tenant_data["conversation"],
        items=[{"name": "Doces", "quantity": "100"}],
    )
    client.force_login(tenant_data["user"])
    url = reverse("api-order-decision", args=[tenant_data["company"].pk, order.pk, "approve"])
    response = client.post(url)
    assert response.status_code == 200
    order.refresh_from_db()
    assert order.status == Order.Status.APPROVED
    assert order.approved_by == tenant_data["user"]
    assert OrderStatusEvent.objects.filter(order=order, new_status=Order.Status.APPROVED).exists()
    assert Message.objects.filter(conversation=order.conversation, external_id="wamid.confirmed").exists()
    assert client.post(url).status_code == 400
    send.assert_called_once()


@pytest.mark.django_db
@patch("saas.api.subscribe_app_to_waba")
@patch("saas.api.verify_coexistence_assets")
@patch("saas.api.exchange_embedded_signup_code", return_value="customer-token")
def test_embedded_signup_requires_coexistence_event(exchange, verify, subscribe, client, tenant_data, settings):
    settings.WHATSAPP_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()
    verify.return_value = {"id": "coex-number", "display_phone_number": "+5511999999999"}
    client.force_login(tenant_data["user"])
    url = f"/api/v1/companies/{tenant_data['company'].pk}/whatsapp/embedded-signup/complete/"
    rejected = client.post(url, {
        "code": "code", "waba_id": "waba", "phone_number_id": "coex-number", "event": "FINISH",
    }, content_type="application/json")
    assert rejected.status_code == 400
    assert not exchange.called
    accepted = client.post(url, {
        "code": "code", "waba_id": "waba", "phone_number_id": "coex-number",
        "event": "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING",
    }, content_type="application/json")
    assert accepted.status_code == 201
    assert accepted.json()["connection_mode"] == "coexistence"
    subscribe.assert_called_once_with("customer-token", "waba")
