from types import SimpleNamespace
from unittest.mock import Mock, patch
from saas.services import answer_with_rag, send_whatsapp_text, split_text

def test_split_text(): assert len(split_text("Uma frase. " * 100, size=100, overlap=20)) > 1

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
