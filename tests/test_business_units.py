import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from importlib import import_module
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.apps import apps
from django.db import connection, connections, close_old_connections
from django.urls import reverse
from django.utils import timezone

from saas.billing import number_capacity
from saas.business_units import create_unit, default_unit
from saas.models import (
    BusinessUnit, CatalogItem, Company, Conversation, KnowledgeBase, KnowledgeChunk,
    KnowledgeDocument, KnowledgeDocumentShare, MediaAsset, Membership, Order, Sale, WhatsAppNumber,
)
from saas.services import answer_with_rag, authorized_chunks, authorized_media


pytestmark = pytest.mark.django_db


def post_json(client, url, payload, **headers):
    return client.post(url, json.dumps(payload), content_type="application/json", **headers)


def test_new_company_gets_default_unit_and_versioned_plan():
    company = Company.objects.create(name="Nova", slug="nova-unit")
    unit = default_unit(company)
    assert unit.name == "Geral" and unit.is_default and unit.is_active
    assert company.business_units.count() == 1
    assert company.plan.version == 2
    assert company.plan.included_business_units == 2
    assert company.plan.included_whatsapp_numbers == 2
    assert company.plan.additional_whatsapp_monthly_price == Decimal("30.00")


def test_unit_limit_counts_inactive(tenant_data):
    company = tenant_data["company"]
    other = create_unit(company, tenant_data["user"], name="Eventos", slug="eventos")
    other.is_active = False
    other.save(update_fields=["is_active"])
    from rest_framework.exceptions import ValidationError
    with pytest.raises(ValidationError, match="limite"):
        create_unit(company, tenant_data["user"], name="Terceira", slug="terceira")
    assert company.business_units.count() == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_unit_creation_cannot_exceed_plan(tenant_data):
    if connection.vendor != "postgresql":
        pytest.skip("O bloqueio concorrente exige PostgreSQL.")
    company, actor = tenant_data["company"], tenant_data["user"]
    barrier = Barrier(2)

    def create(name):
        close_old_connections()
        barrier.wait()
        try:
            create_unit(company, actor, name=name, slug=name.lower())
            return True
        except Exception as exc:
            from rest_framework.exceptions import ValidationError
            if isinstance(exc, ValidationError):
                return False
            raise
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, ("Eventos", "Loja")))
    assert sorted(results) == [False, True]
    assert company.business_units.count() == 2


def test_unit_api_is_company_scoped_and_requires_admin(client, tenant_data):
    company = tenant_data["company"]
    client.force_login(tenant_data["user"])
    response = post_json(client, reverse("api-units", args=[company.pk]), {"name": "Eventos"})
    assert response.status_code == 201, response.content
    unit_id = response.json()["id"]
    assert post_json(client, reverse("api-units", args=[company.pk]), {"name": "Outra"}).status_code == 400
    assert client.get(reverse("api-units", args=[tenant_data["company_b"].pk])).status_code == 404
    patch = client.patch(reverse("api-unit", args=[company.pk, unit_id]), json.dumps({"is_active": False}), content_type="application/json")
    assert patch.status_code == 200
    assert BusinessUnit.objects.get(pk=unit_id).is_active is False


def test_numbers_can_share_or_split_units_and_move_preserves_conversations(client, tenant_data):
    company = tenant_data["company"]
    first = tenant_data["number"]
    first.business_unit = default_unit(company)
    first.save(update_fields=["business_unit"])
    original = tenant_data["conversation"]
    original.business_unit = first.business_unit
    original.save(update_fields=["business_unit"])
    events = create_unit(company, tenant_data["user"], name="Eventos", slug="eventos")
    client.force_login(tenant_data["user"])
    url = reverse("api-whatsapp-numbers", args=[company.pk])
    second = post_json(client, url, {"phone_number_id": "second", "display_phone_number": "+5511999999", "business_unit_id": str(first.business_unit_id)})
    assert second.status_code == 201, second.content
    second_number = WhatsAppNumber.objects.get(pk=second.json()["id"])
    assert second_number.business_unit_id == first.business_unit_id
    moved = client.patch(reverse("api-number-unit", args=[company.pk, second_number.pk]), json.dumps({"business_unit_id": str(events.pk)}), content_type="application/json")
    assert moved.status_code == 200
    original.refresh_from_db()
    second_number.refresh_from_db()
    assert original.business_unit_id == first.business_unit_id
    assert second_number.business_unit_id == events.pk


def test_rag_only_returns_owned_or_explicitly_shared_documents(tenant_data):
    company, other_company = tenant_data["company"], tenant_data["company_b"]
    own_unit = default_unit(company)
    events = create_unit(company, tenant_data["user"], name="Eventos", slug="eventos")
    other_unit = default_unit(other_company)

    def make_chunk(unit, label):
        base = KnowledgeBase.objects.create(company=unit.company, business_unit=unit, name=label)
        doc = KnowledgeDocument.objects.create(company=unit.company, knowledge_base=base, title=label, file=f"{label}.txt", status="ready")
        chunk = KnowledgeChunk.objects.create(company=unit.company, knowledge_base=base, document=doc, position=0, content=label, embedding=[0.1] * 768)
        return doc, chunk

    own_doc, own_chunk = make_chunk(own_unit, "Chopeiras")
    _, event_chunk = make_chunk(events, "Eventos")
    _, other_chunk = make_chunk(other_unit, "Segredo de outra empresa")
    assert list(authorized_chunks(company.pk, events.pk).values_list("pk", flat=True)) == [event_chunk.pk]
    KnowledgeDocumentShare.objects.create(document=own_doc, business_unit=events, granted_by=tenant_data["user"])
    ids = set(authorized_chunks(company.pk, events.pk).values_list("pk", flat=True))
    assert ids == {own_chunk.pk, event_chunk.pk}
    assert other_chunk.pk not in ids


def test_media_scope_and_cross_company_share_rejected(tenant_data):
    company = tenant_data["company"]
    events = create_unit(company, tenant_data["user"], name="Eventos", slug="eventos")
    own = default_unit(company)
    for unit in (own, events):
        MediaAsset.objects.create(company=company, business_unit=unit, name=unit.name, description="Foto", file="fake.jpg", file_type="image", mime_type="image/jpeg", size=1, search_text="foto", status="ready", embedding=[0.1] * 768)
    assert authorized_media(company.pk, own.pk).count() == 1
    assert authorized_media(company.pk, events.pk).count() == 1
    base = KnowledgeBase.objects.create(company=company, business_unit=own, name="Base")
    doc = KnowledgeDocument.objects.create(company=company, knowledge_base=base, title="Doc", file="doc.txt")
    share = KnowledgeDocumentShare(document=doc, business_unit=default_unit(tenant_data["company_b"]))
    from django.core.exceptions import ValidationError
    with pytest.raises(ValidationError):
        share.full_clean()


def test_document_share_api_is_explicit_and_company_scoped(client, tenant_data):
    company = tenant_data["company"]
    own = default_unit(company)
    events = create_unit(company, tenant_data["user"], name="Eventos", slug="eventos")
    base = KnowledgeBase.objects.create(company=company, business_unit=own, name="Base")
    doc = KnowledgeDocument.objects.create(company=company, knowledge_base=base, title="Doc", file="doc.txt")
    url = reverse("api-document-share", args=[company.pk, doc.pk])
    client.force_login(tenant_data["user"])
    assert post_json(client, url, {}).status_code == 400
    assert post_json(client, url, {"business_unit_id": str(default_unit(tenant_data["company_b"]).pk)}).status_code == 400
    assert post_json(client, url, {"business_unit_id": str(events.pk)}).status_code == 201
    assert KnowledgeDocumentShare.objects.filter(document=doc, business_unit=events).exists()
    removed = client.delete(url, json.dumps({"business_unit_id": str(events.pk)}), content_type="application/json")
    assert removed.status_code == 204
    assert not KnowledgeDocumentShare.objects.filter(document=doc, business_unit=events).exists()


def test_agent_cannot_mutate_units(client, tenant_data):
    company = tenant_data["company"]
    Membership.objects.filter(company=company, user=tenant_data["user"]).update(role=Membership.Role.AGENT)
    client.force_login(tenant_data["user"])
    assert client.get(reverse("api-units", args=[company.pk])).status_code == 200
    assert post_json(client, reverse("api-units", args=[company.pk]), {"name": "Eventos"}).status_code == 403


@patch("saas.services.retrieve_context", return_value=[SimpleNamespace(content="Somente eventos")])
@patch("saas.services.gemini_client")
def test_ai_uses_conversation_unit_for_prompt_and_catalog(client_factory, retrieve, tenant_data):
    company = tenant_data["company"]
    events = create_unit(company, tenant_data["user"], name="Eventos", slug="eventos")
    events.ai_instructions = "Atenda como equipe de eventos."
    events.save(update_fields=["ai_instructions"])
    conversation = tenant_data["conversation"]
    conversation.business_unit = events
    conversation.save(update_fields=["business_unit"])
    CatalogItem.objects.create(company=company, business_unit=events, name="Buffet", kind="service")
    CatalogItem.objects.create(company=company, business_unit=default_unit(company), name="Chopeira", kind="product")
    gemini = Mock()
    gemini.models.generate_content.return_value.text = "Temos buffet."
    client_factory.return_value = gemini
    assert answer_with_rag(company.pk, "Qual serviço?", conversation_id=conversation.pk) == "Temos buffet."
    assert retrieve.call_args.kwargs["business_unit_id"] == events.pk
    prompt = gemini.models.generate_content.call_args.kwargs["contents"]
    assert "Atenda como equipe de eventos." in prompt
    assert "Buffet" in prompt and "Chopeira" not in prompt


def test_catalog_rejects_media_from_another_unit(client, tenant_data):
    company = tenant_data["company"]
    own = default_unit(company)
    events = create_unit(company, tenant_data["user"], name="Eventos", slug="eventos")
    media = MediaAsset.objects.create(company=company, business_unit=own, name="Foto", description="Foto", file="foto.jpg", file_type="image", mime_type="image/jpeg", size=1)
    client.force_login(tenant_data["user"])
    url = reverse("api-catalog-items", args=[company.pk])
    result = post_json(client, url, {"name": "Buffet", "kind": "service", "business_unit_id": str(events.pk), "media_assets": [str(media.pk)]})
    assert result.status_code == 400
    assert not CatalogItem.objects.filter(name="Buffet").exists()


def test_webhook_uses_receiving_number_unit_and_keeps_old_conversation(client, tenant_data, settings):
    company = tenant_data["company"]
    number = tenant_data["number"]
    old = tenant_data["conversation"]
    old.business_unit = default_unit(company)
    old.save(update_fields=["business_unit"])
    events = create_unit(company, tenant_data["user"], name="Eventos", slug="eventos")
    number.business_unit = events
    number.save(update_fields=["business_unit"])
    settings.WHATSAPP_APP_SECRET = "test-secret"
    payload = {"entry": [{"changes": [{"field": "messages", "value": {"metadata": {"phone_number_id": number.phone_number_id}, "messages": [{"id": "wamid.events", "from": "55118880000", "type": "text", "text": {"body": "Olá"}}]}}]}]}
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(settings.WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    response = client.post(reverse("whatsapp-webhook"), body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature)
    assert response.status_code == 200
    assert Conversation.objects.get(contact__wa_id="55118880000").business_unit_id == events.pk
    old.refresh_from_db()
    assert old.business_unit_id == default_unit(company).pk


def test_manual_sale_requires_unit_and_dashboard_can_consolidate(client, tenant_data):
    company = tenant_data["company"]
    events = create_unit(company, tenant_data["user"], name="Eventos", slug="eventos")
    client.force_login(tenant_data["user"])
    payload = {"contact_id": tenant_data["contact"].pk, "sold_at": timezone.now().isoformat(), "lines": [{"name": "Serviço", "kind": "service", "quantity": "1", "unit_price": "10.00"}]}
    url = reverse("api-sales", args=[company.pk])
    assert post_json(client, url, payload, HTTP_IDEMPOTENCY_KEY="sale-no-unit").status_code == 400
    payload["business_unit_id"] = str(events.pk)
    result = post_json(client, url, payload, HTTP_IDEMPOTENCY_KEY="sale-events")
    assert result.status_code == 201, result.content
    assert Sale.objects.get(pk=result.json()["id"]).business_unit_id == events.pk
    payload["business_unit_id"] = str(default_unit(company).pk)
    payload["lines"][0]["unit_price"] = "7.00"
    second = post_json(client, url, payload, HTTP_IDEMPOTENCY_KEY="sale-general")
    assert second.status_code == 201, second.content
    scoped = client.get(reverse("api-finance-dashboard", args=[company.pk]), {"business_unit_id": str(events.pk)}).json()
    consolidated = client.get(reverse("api-finance-dashboard", args=[company.pk])).json()
    assert scoped["registered_revenue"] == "10.00"
    assert consolidated["registered_revenue"] == "17.00"
    csv_url = reverse("api-sales-report", args=[company.pk])
    scoped_csv = client.get(csv_url, {"business_unit_id": str(events.pk)}).content.decode()
    company_csv = client.get(csv_url).content.decode()
    assert str(result.json()["id"]) in scoped_csv and str(second.json()["id"]) not in scoped_csv
    assert str(second.json()["id"]) in company_csv


def test_approved_order_sale_inherits_historical_conversation_unit(client, tenant_data):
    company = tenant_data["company"]
    events = create_unit(company, tenant_data["user"], name="Eventos", slug="eventos")
    conversation = tenant_data["conversation"]
    conversation.business_unit = events
    conversation.save(update_fields=["business_unit"])
    order = Order.objects.create(company=company, business_unit=events, conversation=conversation, status=Order.Status.APPROVED)
    client.force_login(tenant_data["user"])
    payload = {"contact_id": tenant_data["contact"].pk, "order_id": str(order.pk), "sold_at": timezone.now().isoformat(), "lines": [{"name": "Evento", "kind": "service", "quantity": "1", "unit_price": "20.00"}]}
    result = post_json(client, reverse("api-sales", args=[company.pk]), payload, HTTP_IDEMPOTENCY_KEY="sale-order-events")
    assert result.status_code == 201, result.content
    assert Sale.objects.get(pk=result.json()["id"]).business_unit_id == events.pk


def test_backfill_preserves_existing_number_links(tenant_data):
    company = tenant_data["company"]
    number = tenant_data["number"]
    conversation = tenant_data["conversation"]
    company.plan = None
    company.save(update_fields=["plan"])
    number.business_unit = None
    number.save(update_fields=["business_unit"])
    conversation.business_unit = None
    conversation.save(update_fields=["business_unit"])
    migration = import_module("saas.migrations.0021_businessunit_catalogitem_business_unit_and_more")
    migration.backfill_units_and_plans(apps, None)
    company.refresh_from_db()
    number.refresh_from_db()
    conversation.refresh_from_db()
    assert company.plan.version == 1
    assert company.plan.additional_whatsapp_monthly_price == Decimal("49.90")
    assert number.business_unit_id == default_unit(company).pk
    assert conversation.business_unit_id == number.business_unit_id
    assert number_capacity(company) is None
