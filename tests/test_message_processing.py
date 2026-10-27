from unittest.mock import Mock, patch
from django.utils import timezone

import pytest

from saas.models import Conversation, Message
from saas.tasks import analyze_conversation, answer_inbound_message


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


@patch("saas.tasks.analyze_sales_conversation")
def test_sales_analysis_updates_follow_up(analysis, tenant_data):
    tenant_data["conversation"].last_message_at = timezone.now()
    tenant_data["conversation"].save(update_fields=["last_message_at"])
    analysis.return_value = {
        "temperature": "hot",
        "score": 91,
        "summary": "Cliente pediu proposta para contratar.",
        "buying_signals": ["Pediu uma proposta"],
        "objections": ["Prazo de implantação"],
        "recommended_action": "Enviar proposta e confirmar prazo.",
    }

    analyze_conversation.run(tenant_data["conversation"].pk)

    tenant_data["conversation"].refresh_from_db()
    assert tenant_data["conversation"].lead_temperature == Conversation.LeadTemperature.HOT
    assert tenant_data["conversation"].purchase_intent_score == 91
    assert tenant_data["conversation"].next_follow_up_at is not None
