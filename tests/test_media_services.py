import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from saas.services import (
    download_whatsapp_audio,
    select_media_for_question,
    send_whatsapp_media,
    transcribe_audio,
)


@patch("saas.services.gemini_client")
def test_transcribe_ogg_audio_without_external_call(client_factory, settings):
    settings.GEMINI_AUDIO_MODEL = "gemini-3.1-flash-lite"
    settings.GEMINI_AUDIO_MAX_OUTPUT_TOKENS = 500
    client = Mock()
    client.models.generate_content.return_value = SimpleNamespace(
        text="Quero conhecer a chopeira.", usage_metadata=None
    )
    client_factory.return_value = client

    assert transcribe_audio(b"OggS-fake-audio", "audio/ogg; codecs=opus") == "Quero conhecer a chopeira."
    call = client.models.generate_content.call_args.kwargs
    assert call["model"] == settings.GEMINI_AUDIO_MODEL
    assert call["config"].max_output_tokens == 500
    assert call["contents"][1].inline_data.mime_type == "audio/ogg"


def test_transcribe_audio_rejects_invalid_format(settings):
    with pytest.raises(ValueError, match="não suportado"):
        transcribe_audio(b"bad", "application/octet-stream")


@patch("saas.services.httpx.get")
def test_audio_download_rejects_non_meta_url(get, settings):
    metadata = Mock()
    metadata.json.return_value = {"url": "https://attacker.example/audio.ogg", "mime_type": "audio/ogg"}
    get.return_value = metadata
    with pytest.raises(ValueError, match="URL de mídia inválida"):
        download_whatsapp_audio("media-id")


@patch("saas.services.gemini_client")
@patch("saas.services.search_media")
def test_media_selection_accepts_only_returned_real_id(search, client_factory):
    asset = SimpleNamespace(
        pk="2dc36c18-156c-44c5-a227-1015f6fa6711",
        name="Foto chopeira 50L",
        description="Foto frontal",
        file_type="image",
        product_service="Chopeira 50L",
    )
    search.return_value = [asset]
    client = Mock()
    client.models.generate_content.return_value = SimpleNamespace(
        text=json.dumps({"media_id": str(asset.pk)}), usage_metadata=None
    )
    client_factory.return_value = client
    assert select_media_for_question(1, "Tem foto da chopeira de 50 litros?") is asset

    client.models.generate_content.return_value = SimpleNamespace(
        text=json.dumps({"media_id": "id-inventado"}), usage_metadata=None
    )
    assert select_media_for_question(1, "Tem foto da chopeira?") is None


@patch("saas.services.search_media")
def test_media_selection_does_not_use_recent_context_as_permission(search):
    assert select_media_for_question(
        1,
        "Sim, por favor",
        "ATENDENTE: Quer que eu envie uma foto do modelo personalizado?",
    ) is None
    search.assert_not_called()


@patch("saas.services.search_media")
def test_media_selection_does_not_send_for_product_details_without_request(search):
    assert select_media_for_question(
        1,
        "A chopeira pode ser personalizada com minha logo e minhas cores?",
        "CLIENTE: Quero conhecer melhor a chopeira.",
    ) is None
    search.assert_not_called()


@patch("saas.services.httpx.post")
def test_send_whatsapp_image_uses_existing_meta_id(post, settings):
    response = Mock(status_code=200)
    response.json.return_value = {"messages": [{"id": "wamid.media"}]}
    post.return_value = response
    number = SimpleNamespace(company_id=1, phone_number_id="number-id")
    asset = SimpleNamespace(
        company_id=1,
        is_active=True,
        allow_ai_send=True,
        meta_media_id="meta-media-id",
        meta_media_status="valid",
        file_type="image",
        name="Produto",
        save=Mock(),
    )
    assert send_whatsapp_media(number, "5511999", asset) == "wamid.media"
    payload = post.call_args.kwargs["json"]
    assert payload["type"] == "image"
    assert payload["image"]["id"] == "meta-media-id"


def test_send_whatsapp_media_blocks_cross_tenant(settings):
    number = SimpleNamespace(company_id=1, phone_number_id="number-id")
    asset = SimpleNamespace(company_id=2, is_active=True, allow_ai_send=True)
    with pytest.raises(ValueError, match="não autorizada"):
        send_whatsapp_media(number, "5511999", asset)
