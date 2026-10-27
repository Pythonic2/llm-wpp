from types import SimpleNamespace
from unittest.mock import Mock, patch
from saas.services import answer_with_rag, greeting_response, send_whatsapp_text, split_text

def test_split_text(): assert len(split_text("Uma frase. " * 100, size=100, overlap=20)) > 1

def test_greeting_response_recognizes_simple_greetings():
    assert greeting_response("Oi!") == "Olá! Tudo bem? Como posso ajudar você hoje?"
    assert greeting_response("BOA TARDE") == "Olá! Tudo bem? Como posso ajudar você hoje?"
    assert greeting_response("Olá, quero saber o prazo") is None

@patch("saas.services.retrieve_context")
def test_greeting_does_not_query_knowledge_base(retrieve):
    assert answer_with_rag("tenant", "Olá!") == "Olá! Tudo bem? Como posso ajudar você hoje?"
    retrieve.assert_not_called()

@patch("saas.services.retrieve_context")
@patch("saas.services.gemini_client")
def test_rag_uses_retrieved_context(client_factory, retrieve, settings):
    retrieve.return_value = [SimpleNamespace(content="A política permite troca em 7 dias.")]
    client = Mock(); client.models.generate_content.return_value.text = "A troca pode ser feita em 7 dias."
    client_factory.return_value = client
    assert answer_with_rag("tenant", "Qual o prazo?") == "A troca pode ser feita em 7 dias."
    assert "A política permite troca em 7 dias." in client.models.generate_content.call_args.kwargs["contents"]

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
