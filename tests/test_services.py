from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from saas.services import (
    _generation_config,
    _log_gemini_usage,
    analyze_sales_conversation,
    answer_with_rag,
    greeting_response,
    send_whatsapp_text,
    split_text,
)

def test_split_text(): assert len(split_text("Uma frase. " * 100, size=100, overlap=20)) > 1

def test_greeting_response_recognizes_simple_greetings():
    assert greeting_response("Oi!") == "Olá! Tudo bem? Como posso ajudar você hoje?"
    assert greeting_response("BOA TARDE") == "Olá! Tudo bem? Como posso ajudar você hoje?"
    assert greeting_response("Olá, quero saber o prazo") is None


def test_customer_generation_config_limits_output_and_thinking(settings):
    settings.GEMINI_CHAT_MODEL = "gemini-3.1-flash-lite"
    settings.GEMINI_THINKING_LEVEL = "minimal"
    config = _generation_config(200)
    assert config.max_output_tokens == 200
    assert config.thinking_config.thinking_level.value == "MINIMAL"


def test_generation_config_omits_thinking_for_incompatible_model(settings):
    settings.GEMINI_CHAT_MODEL = "gemini-2.5-flash"
    settings.GEMINI_THINKING_LEVEL = "minimal"
    config = _generation_config(200)
    assert config.max_output_tokens == 200
    assert config.thinking_config is None


def test_usage_log_handles_metadata_and_configurable_cost(caplog, settings):
    settings.GEMINI_CHAT_MODEL = "gemini-test"
    settings.GEMINI_INPUT_COST_PER_MILLION = 1
    settings.GEMINI_OUTPUT_COST_PER_MILLION = 2
    settings.GEMINI_THINKING_COST_PER_MILLION = 3
    response = SimpleNamespace(usage_metadata=SimpleNamespace(
        prompt_token_count=100,
        candidates_token_count=20,
        thoughts_token_count=10,
        total_token_count=130,
    ))
    with caplog.at_level("INFO", logger="saas.services"):
        _log_gemini_usage(response, 0.25, "test")
    assert "input_tokens=100" in caplog.text
    assert "thinking_tokens=10" in caplog.text
    assert "estimated_cost_usd=0.00017000" in caplog.text


def test_usage_log_handles_missing_metadata(caplog, settings):
    settings.GEMINI_INPUT_COST_PER_MILLION = 0
    settings.GEMINI_OUTPUT_COST_PER_MILLION = 0
    settings.GEMINI_THINKING_COST_PER_MILLION = 0
    with caplog.at_level("INFO", logger="saas.services"):
        _log_gemini_usage(SimpleNamespace(), 0.1, "test")
    assert "total_tokens=0" in caplog.text

@patch("saas.services.retrieve_context")
@patch("saas.services.gemini_client")
@pytest.mark.django_db
def test_greeting_uses_company_instructions_without_querying_knowledge_base(client_factory, retrieve, tenant_data):
    client = Mock()
    client.models.generate_content.return_value.text = "Olá! Como posso ajudar?"
    client_factory.return_value = client

    assert answer_with_rag(tenant_data["company"].pk, "Olá!") == "Olá! Como posso ajudar?"
    retrieve.assert_not_called()
    prompt = client.models.generate_content.call_args.kwargs["contents"]
    assert tenant_data["company"].ai_instructions in prompt

@patch("saas.services.retrieve_context")
@patch("saas.services.gemini_client")
@pytest.mark.django_db
def test_rag_uses_retrieved_context(client_factory, retrieve, settings, tenant_data):
    tenant_data["company"].ai_instructions = "Atenda como uma confeiteira alegre e objetiva."
    tenant_data["company"].save(update_fields=["ai_instructions"])
    retrieve.return_value = [SimpleNamespace(content="A política permite troca em 7 dias.")]
    client = Mock(); client.models.generate_content.return_value.text = "A troca pode ser feita em 7 dias."
    client_factory.return_value = client
    assert answer_with_rag(tenant_data["company"].pk, "Qual o prazo?") == "A troca pode ser feita em 7 dias."
    prompt = client.models.generate_content.call_args.kwargs["contents"]
    config = client.models.generate_content.call_args.kwargs["config"]
    assert "A política permite troca em 7 dias." in prompt
    assert "Atenda como uma confeiteira alegre e objetiva." in prompt
    assert "REGRAS OBRIGATÓRIAS DO SISTEMA" in prompt
    assert "use exclusivamente o conteúdo" in prompt
    assert config.max_output_tokens == settings.GEMINI_MAX_OUTPUT_TOKENS

@patch("saas.services.retrieve_context")
@patch("saas.services.gemini_client")
@pytest.mark.django_db
def test_rag_removes_context_introduction_from_answer(client_factory, retrieve, settings, tenant_data):
    retrieve.return_value = [SimpleNamespace(content="A empresa oferece consultoria e suporte.")]
    client = Mock()
    client.models.generate_content.return_value.text = (
        "Com base no contexto, os principais serviços oferecidos pela empresa são consultoria e suporte."
    )
    client_factory.return_value = client

    assert answer_with_rag(tenant_data["company"].pk, "Quais serviços vocês oferecem?") == (
        "Os principais serviços oferecidos pela empresa são consultoria e suporte."
    )


@patch("saas.models.Message.objects.filter")
@patch("saas.models.Conversation.objects.select_related")
@patch("saas.services.gemini_client")
def test_sales_analysis_keeps_gemini_client_alive(client_factory, select_related, message_filter, settings):
    conversation = SimpleNamespace(pk=7, contact=SimpleNamespace(name="Cliente"))
    select_related.return_value.get.return_value = conversation
    message_filter.return_value.exclude.return_value.order_by.return_value.__getitem__.return_value = []
    client = Mock()
    client.models.generate_content.return_value.text = (
        '{"temperature":"warm","score":60,"summary":"Tem interesse.",'
        '"buying_signals":["Perguntou sobre a solução"],"objections":[],'
        '"recommended_action":"Entender o prazo."}'
    )
    client_factory.return_value = client

    result = analyze_sales_conversation(conversation.pk)

    assert result["temperature"] == "warm"
    client.models.generate_content.assert_called_once()
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.max_output_tokens == settings.GEMINI_ANALYSIS_MAX_OUTPUT_TOKENS
    assert config.response_mime_type == "application/json"

@patch("saas.services.httpx.post")
def test_whatsapp_sender_uses_cloud_api(post, settings):
    settings.WHATSAPP_ACCESS_TOKEN = "token"
    response = Mock(); response.json.return_value = {"messages": [{"id": "wamid.out"}]}; post.return_value = response
    assert send_whatsapp_text(SimpleNamespace(phone_number_id="test-number"), "5511999", "Resposta") == "wamid.out"
    assert post.call_args.kwargs["json"]["messaging_product"] == "whatsapp"
    response.raise_for_status.assert_called_once()

@patch("saas.services.httpx.post")
def test_whatsapp_sender_retries_brazilian_mobile_with_ninth_digit(post, settings):
    settings.WHATSAPP_ACCESS_TOKEN = "token"
    rejected = Mock(status_code=400)
    rejected.json.return_value = {"error": {"code": 131030}}
    accepted = Mock(status_code=200)
    accepted.json.return_value = {"messages": [{"id": "wamid.out"}]}
    post.side_effect = [rejected, accepted]

    assert send_whatsapp_text(SimpleNamespace(phone_number_id="test-number"), "558398413751", "Resposta") == "wamid.out"
    assert post.call_count == 2
    assert post.call_args_list[0].kwargs["json"]["to"] == "558398413751"
    assert post.call_args_list[1].kwargs["json"]["to"] == "5583998413751"
