import hashlib
import hmac
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from django.urls import reverse

from saas.models import Message

pytestmark = pytest.mark.django_db


def _send(client, settings, data):
    settings.WHATSAPP_APP_SECRET = "test-secret"
    body = json.dumps(data).encode()
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    return client.post(reverse("whatsapp-webhook"), body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature)


def _payload(items):
    return {"entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": "meta-test-id"}, "messages": items,
    }}]}]}


@patch("saas.webhooks.store_inbound_attachment.delay")
@patch("saas.webhooks.answer_inbound_message.delay")
def test_video_has_original_time_and_is_downloadable_without_storage_job(answer, store, client, settings, tenant_data):
    timestamp = "1770000000"
    data = _payload([{"id": "wamid.video", "from": "5511999999999", "type": "video",
                      "timestamp": timestamp, "video": {"id": "meta-video", "mime_type": "video/mp4"}}])
    assert _send(client, settings, data).status_code == 200
    message = Message.objects.get(external_id="wamid.video")
    assert message.sent_at == datetime.fromtimestamp(int(timestamp), timezone.utc)
    assert message.source_media_id == "meta-video"
    assert not message.attachment
    store.assert_not_called()
    answer.assert_not_called()
    client.force_login(tenant_data["user"])
    url = reverse("api-conversation-messages", kwargs={"company_id": tenant_data["company"].pk,
                                                         "conversation_id": message.conversation_id})
    result = client.get(url).json()["results"]
    video = next(item for item in result if item["id"] == message.pk)
    assert video["attachment_url"]
    assert video["sent_at"]


@patch("saas.api.download_whatsapp_attachment", return_value=(b"video-bytes", "video/mp4"))
def test_media_download_is_authenticated_and_tenant_scoped(download, client, tenant_data):
    conversation = tenant_data["conversation"]
    message = Message.objects.create(company=tenant_data["company"], conversation=conversation,
                                     external_id="wamid.media", direction="in", message_type="video",
                                     source_media_id="meta-video")
    url = reverse("api-message-attachment", kwargs={"company_id": tenant_data["company"].pk,
                                                    "conversation_id": conversation.pk, "message_id": message.pk})
    assert client.get(url).status_code in {401, 403}
    client.force_login(tenant_data["other"])
    assert client.get(url).status_code in {403, 404}
    download.assert_not_called()
    client.force_login(tenant_data["user"])
    response = client.get(url)
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"video-bytes"
    assert "attachment" in response["Content-Disposition"]
    assert not Message.objects.get(pk=message.pk).attachment
    download.assert_called_once()


def test_messages_order_by_original_time(client, tenant_data):
    conversation = tenant_data["conversation"]
    later = Message.objects.create(company=tenant_data["company"], conversation=conversation,
                                   external_id="wamid.later", direction="in", sent_at=datetime(2026, 2, 2, tzinfo=timezone.utc))
    earlier = Message.objects.create(company=tenant_data["company"], conversation=conversation,
                                     external_id="wamid.earlier", direction="out", sent_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
    client.force_login(tenant_data["user"])
    url = reverse("api-conversation-messages", kwargs={"company_id": tenant_data["company"].pk,
                                                         "conversation_id": conversation.pk})
    assert [item["id"] for item in client.get(url).json()["results"]] == [earlier.pk, later.pk]


def test_timestamp_backfill_preserves_existing_values(tenant_data):
    from django.apps import apps
    from django.db import connection
    from importlib import import_module

    conversation = tenant_data["conversation"]
    historical = Message.objects.create(company=tenant_data["company"], conversation=conversation,
                                        external_id="wamid.historical", direction="in",
                                        raw_payload={"timestamp": "1770000000"})
    already_set = Message.objects.create(company=tenant_data["company"], conversation=conversation,
                                         external_id="wamid.existing-time", direction="out",
                                         sent_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                         raw_payload={"timestamp": "1770000000"})
    migration = import_module("saas.migrations.0018_backfill_message_sent_at")
    with connection.schema_editor() as editor:
        migration.backfill_sent_at(apps, editor)
    historical.refresh_from_db()
    already_set.refresh_from_db()
    assert historical.sent_at == datetime.fromtimestamp(1770000000, timezone.utc)
    assert already_set.sent_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
