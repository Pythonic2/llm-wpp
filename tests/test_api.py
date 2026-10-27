from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from saas.models import (
    Contact,
    KnowledgeBase,
    KnowledgeDocument,
    Membership,
    Message,
    WhatsAppNumber,
)

pytestmark = pytest.mark.django_db


def api_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def company_url(company_id, path=""):
    return f"/api/v1/companies/{company_id}/{path}"


def test_company_and_dashboard_are_limited_to_membership(tenant_data):
    response = api_client(tenant_data["user"]).get(company_url(tenant_data["company"].pk, "dashboard/"))
    assert response.status_code == 200
    assert response.data["total_conversations"] == 1
    assert response.data["ai_conversations"] == 0

    Message.objects.create(
        company=tenant_data["company"],
        conversation=tenant_data["conversation"],
        external_id="wamid.ai-dashboard",
        direction=Message.Direction.INBOUND,
        ai_status=Message.AIStatus.COMPLETED,
    )
    response = api_client(tenant_data["user"]).get(company_url(tenant_data["company"].pk, "dashboard/"))
    assert response.data["ai_conversations"] == 1
    assert api_client(tenant_data["user"]).get(company_url(tenant_data["company_b"].pk)).status_code == 404


def test_agent_can_read_contacts_but_cannot_manage_whatsapp(tenant_data):
    Membership.objects.filter(company=tenant_data["company"], user=tenant_data["user"]).update(role="agent")
    client = api_client(tenant_data["user"])

    response = client.get(company_url(tenant_data["company"].pk, "contacts/"))
    assert response.status_code == 200
    assert [item["id"] for item in response.data["results"]] == [tenant_data["contact"].pk]

    response = client.get(company_url(tenant_data["company"].pk, "whatsapp-numbers/"))
    assert response.status_code == 200
    number = response.data["results"][0]

    response = client.patch(
        company_url(tenant_data["company"].pk, f"whatsapp-numbers/{number['id']}/"),
        {"ai_enabled": False},
        format="json",
    )
    assert response.status_code == 403


def test_conversation_assignment_rejects_user_from_another_company(tenant_data):
    response = api_client(tenant_data["user"]).post(
        company_url(
            tenant_data["company"].pk,
            f"conversations/{tenant_data['conversation'].pk}/assign/",
        ),
        {"user_id": tenant_data["other"].pk},
        format="json",
    )
    assert response.status_code == 400
    assert not tenant_data["conversation"].assigned_to


def test_manual_message_sends_once_and_pauses_ai(tenant_data):
    client = api_client(tenant_data["user"])
    path = company_url(
        tenant_data["company"].pk,
        f"conversations/{tenant_data['conversation'].pk}/messages/",
    )
    with patch("saas.api.conversation_ai_lock") as lock_factory, patch(
        "saas.api.send_whatsapp_text", return_value="wamid.manual"
    ) as send:
        lock_factory.return_value.acquire.return_value = True
        first = client.post(
            path,
            {"text": "Olá, posso ajudar?"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="message-once",
        )
        replay = client.post(
            path,
            {"text": "Olá, posso ajudar?"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="message-once",
        )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.data["external_id"] == "wamid.manual"
    assert first.data["status"] == Message.Status.SENT
    assert Message.objects.filter(conversation=tenant_data["conversation"]).count() == 1
    tenant_data["conversation"].refresh_from_db()
    assert tenant_data["conversation"].ai_paused
    send.assert_called_once()


def test_manual_message_generates_idempotency_key_when_header_is_missing(tenant_data):
    with patch("saas.api.conversation_ai_lock") as lock_factory, patch(
        "saas.api.send_whatsapp_text", return_value="wamid.with-server-key"
    ):
        lock_factory.return_value.acquire.return_value = True
        response = api_client(tenant_data["user"]).post(
            company_url(
                tenant_data["company"].pk,
                f"conversations/{tenant_data['conversation'].pk}/messages/",
            ),
            {"text": "Mensagem sem chave do navegador"},
            format="json",
        )

    assert response.status_code == 201
    message = Message.objects.get(external_id="wamid.with-server-key")
    assert message.idempotency_key.startswith("server:")


def test_manual_message_failure_is_persisted(tenant_data):
    with patch("saas.api.conversation_ai_lock") as lock_factory, patch(
        "saas.api.send_whatsapp_text", side_effect=RuntimeError("offline")
    ):
        lock_factory.return_value.acquire.return_value = True
        response = api_client(tenant_data["user"]).post(
            company_url(
                tenant_data["company"].pk,
                f"conversations/{tenant_data['conversation'].pk}/messages/",
            ),
            {"text": "Mensagem"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="message-failed",
        )
    assert response.status_code == 502
    message = Message.objects.get(idempotency_key="message-failed")
    assert message.status == Message.Status.FAILED


def test_manual_message_does_not_race_with_ai_lock(tenant_data):
    with patch("saas.api.conversation_ai_lock") as lock_factory:
        lock_factory.return_value.acquire.return_value = False
        response = api_client(tenant_data["user"]).post(
            company_url(
                tenant_data["company"].pk,
                f"conversations/{tenant_data['conversation'].pk}/messages/",
            ),
            {"text": "Mensagem"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="message-during-ai",
        )

    assert response.status_code == 409
    assert not Message.objects.filter(idempotency_key="message-during-ai").exists()
    tenant_data["conversation"].refresh_from_db()
    assert not tenant_data["conversation"].ai_paused


def test_contact_search_is_scoped_to_company(tenant_data):
    Contact.objects.create(company=tenant_data["company_b"], wa_id="term", name="Cliente Especial")
    response = api_client(tenant_data["user"]).get(
        company_url(tenant_data["company"].pk, "contacts/?search=Cliente%20Especial")
    )
    assert response.status_code == 200
    assert response.data["count"] == 0


def test_contact_crud_uses_whatsapp_id_only_on_creation(tenant_data):
    client = api_client(tenant_data["user"])
    path = company_url(tenant_data["company"].pk, "contacts/")
    created = client.post(
        path,
        {"name": "Novo contato", "phone": "+5511987654321", "wa_id": "5511987654321"},
        format="json",
    )
    assert created.status_code == 201
    contact_id = created.data["id"]
    assert created.data["wa_id"] == "5511987654321"

    detail = client.get(f"{path}{contact_id}/")
    assert detail.status_code == 200
    updated = client.patch(
        f"{path}{contact_id}/",
        {"name": "Nome atualizado", "phone": "+5511976543210", "wa_id": "nao-alterar"},
        format="json",
    )
    assert updated.status_code == 200
    assert updated.data["name"] == "Nome atualizado"
    assert updated.data["wa_id"] == "5511987654321"

    duplicate = client.post(
        path,
        {"name": "Duplicado", "wa_id": "5511987654321"},
        format="json",
    )
    assert duplicate.status_code == 400
    assert "wa_id" in duplicate.data

    protected_delete = client.delete(f"{path}{tenant_data['contact'].pk}/")
    assert protected_delete.status_code == 400
    assert "relacionado" in protected_delete.data["detail"]


def test_document_upload_validates_extension_and_schedules_processing(
    tenant_data, tmp_path, settings, django_capture_on_commit_callbacks
):
    settings.MEDIA_ROOT = tmp_path
    knowledge_base = KnowledgeBase.objects.create(company=tenant_data["company"], name="Atendimento")
    client = api_client(tenant_data["user"])
    path = company_url(tenant_data["company"].pk, f"knowledge-bases/{knowledge_base.pk}/documents/")
    invalid = client.post(
        path,
        {"title": "Executável", "file": SimpleUploadedFile("script.exe", b"no")},
        format="multipart",
    )
    assert invalid.status_code == 400

    with patch("saas.api.process_document.delay") as enqueue:
        with django_capture_on_commit_callbacks(execute=True):
            valid = client.post(
                path,
                {"title": "Política", "file": SimpleUploadedFile("politica.txt", b"Conteudo")},
                format="multipart",
            )
    assert valid.status_code == 201
    document = KnowledgeDocument.objects.get(pk=valid.data["id"])
    assert document.company_id == tenant_data["company"].pk
    assert document.status == KnowledgeDocument.Status.PENDING
    enqueue.assert_called_once_with(document.pk)
    document.file.delete(save=False)


def test_document_reprocess_is_company_scoped_and_queued(tenant_data, django_capture_on_commit_callbacks):
    base = KnowledgeBase.objects.create(company=tenant_data["company"], name="Reprocessamento")
    other_base = KnowledgeBase.objects.create(company=tenant_data["company_b"], name="Privada")
    document = KnowledgeDocument.objects.create(
        company=tenant_data["company"],
        knowledge_base=base,
        title="Guia",
        file=SimpleUploadedFile("guia.txt", b"Conteudo"),
        status=KnowledgeDocument.Status.FAILED,
        error="Falha temporária",
    )
    client = api_client(tenant_data["user"])
    path = company_url(
        tenant_data["company"].pk,
        f"knowledge-bases/{base.pk}/documents/{document.pk}/reprocess/",
    )
    with patch("saas.api.process_document.delay") as enqueue:
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(path)

    assert response.status_code == 202
    document.refresh_from_db()
    assert document.status == KnowledgeDocument.Status.PENDING
    assert document.error == ""
    enqueue.assert_called_once_with(document.pk)

    cross_company = client.post(
        company_url(
            tenant_data["company_b"].pk,
            f"knowledge-bases/{other_base.pk}/documents/{document.pk}/reprocess/",
        )
    )
    assert cross_company.status_code == 404
    document.file.delete(save=False)


def test_member_can_only_be_added_to_current_company(tenant_data):
    response = api_client(tenant_data["user"]).post(
        company_url(tenant_data["company"].pk, "members/"),
        {"username": tenant_data["other"].username, "role": "agent"},
        format="json",
    )
    assert response.status_code == 201
    assert Membership.objects.filter(company=tenant_data["company"], user=tenant_data["other"]).exists()


def test_auth_login_and_session_endpoints(client):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user("login-user", password="test-password")
    csrf = client.get("/api/v1/auth/csrf/")
    assert csrf.status_code == 200
    response = client.post(
        "/api/v1/auth/login/",
        {"username": user.username, "password": "test-password"},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert client.get("/api/v1/auth/me/").status_code == 200
    assert client.post("/api/v1/auth/refresh/").status_code == 200
    assert client.post("/api/v1/auth/logout/").status_code == 204


def test_session_login_requires_csrf_and_accepts_email(db):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(
        "csrf-user", email="csrf@example.com", password="secure-test-password"
    )
    client = APIClient(enforce_csrf_checks=True)
    csrf_response = client.get("/api/v1/auth/csrf/")
    csrf_token = csrf_response.cookies["csrftoken"].value

    rejected = client.post(
        "/api/v1/auth/login/",
        {"username": user.email, "password": "secure-test-password"},
        format="json",
    )
    assert rejected.status_code == 403

    accepted = client.post(
        "/api/v1/auth/login/",
        {"username": user.email, "password": "secure-test-password"},
        format="json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert accepted.status_code == 200
    assert accepted.data["user"]["username"] == user.username
