from unittest.mock import Mock, patch

import pytest

from saas.models import Message
from saas.tasks import answer_inbound_message


pytestmark = pytest.mark.django_db


def inbound(tenant_data, external_id="wamid.in"):
    return Message.objects.create(
        company=tenant_data["company"],
        conversation=tenant_data["conversation"],
        external_id=external_id,
        direction=Message.Direction.INBOUND,
        message_type="text",
        text="Olá",
        ai_status=Message.AIStatus.QUEUED,
    )


def redis_with_lock():
    lock = Mock()
    lock.acquire.return_value = True
    client = Mock()
    client.lock.return_value = lock
    return client


@patch("saas.tasks.send_whatsapp_text", return_value="wamid.out")
@patch("saas.tasks.answer_with_rag", return_value="Resposta")
@patch("saas.tasks.conversation_ai_lock")
def test_ai_response_is_idempotent(lock_factory, answer, send, tenant_data):
    lock_factory.return_value = redis_with_lock().lock.return_value
    message = inbound(tenant_data)

    answer_inbound_message.run(message.pk)
    answer_inbound_message.run(message.pk)

    message.refresh_from_db()
    assert message.ai_status == Message.AIStatus.COMPLETED
    assert Message.objects.filter(response_to=message).count() == 1
    answer.assert_called_once()
    send.assert_called_once()


@patch("saas.tasks.answer_with_rag")
@patch("saas.tasks.conversation_ai_lock")
def test_human_takeover_skips_ai(lock_factory, answer, tenant_data):
    lock_factory.return_value = redis_with_lock().lock.return_value
    tenant_data["conversation"].ai_paused = True
    tenant_data["conversation"].save(update_fields=["ai_paused"])
    message = inbound(tenant_data)

    answer_inbound_message.run(message.pk)

    message.refresh_from_db()
    assert message.ai_status == Message.AIStatus.SKIPPED
    answer.assert_not_called()
