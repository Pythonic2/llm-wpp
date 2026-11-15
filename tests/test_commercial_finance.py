import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from saas.models import CatalogItem, Contact, Membership, Message, Order, PaymentMethod, Sale, SalePayment


def post_json(client, url, payload, key="sale-1"):
    return client.post(url, json.dumps(payload), content_type="application/json", HTTP_IDEMPOTENCY_KEY=key)


def sale_payload(contact, *, conversation=None, order=None, lines=None, sold_at=None):
    return {
        "contact_id": contact.pk,
        "conversation_id": conversation.pk if conversation else None,
        "order_id": str(order.pk) if order else None,
        "sold_at": (sold_at or timezone.now()).isoformat(),
        "lines": lines or [{"name": "Serviço avulso", "kind": "service", "quantity": "1", "unit_price": "150.00"}],
    }


@pytest.mark.django_db
def test_manual_service_sale_needs_no_whatsapp_cost_fee_or_tax(client, tenant_data):
    company = tenant_data["company"]
    client.force_login(tenant_data["user"])
    contact_response = post_json(client, reverse("api-contacts", args=[company.pk]), {"name": "Cliente externo", "phone": "11999990000"})
    assert contact_response.status_code == 201
    contact = Contact.objects.get(pk=contact_response.json()["id"])
    assert contact.wa_id == ""
    url = reverse("api-sales", args=[company.pk])
    result = post_json(client, url, sale_payload(contact))
    assert result.status_code == 201, result.content
    data = result.json()
    assert data["conversation"] is None
    assert data["attribution"] == "external"
    assert data["total"] == "150.00"
    assert data["financials"]["cost_data"] == "absent"
    assert data["financials"]["estimated_result"] is None


@pytest.mark.django_db
def test_mixed_sale_snapshots_negotiated_price_and_multiple_sales(client, tenant_data):
    company = tenant_data["company"]
    client.force_login(tenant_data["user"])
    catalog_url = reverse("api-catalog-items", args=[company.pk])
    product = post_json(client, catalog_url, {"name": "Chopeira", "kind": "product", "price_mode": "fixed", "sale_price": "200.00"}).json()
    service = post_json(client, catalog_url, {"name": "Instalação", "kind": "service", "price_mode": "quote"}).json()
    lines = [
        {"catalog_item_id": product["id"], "quantity": "2", "unit_price": "180.00", "discount_amount": "10.00"},
        {"catalog_item_id": service["id"], "quantity": "1", "unit_price": "70.00"},
    ]
    url = reverse("api-sales", args=[company.pk])
    payload = sale_payload(tenant_data["contact"], conversation=tenant_data["conversation"], lines=lines)
    payload["discount_amount"] = "20.00"
    result = post_json(client, url, payload)
    assert result.status_code == 201, result.content
    assert result.json()["total"] == "400.00"
    assert {line["kind"] for line in result.json()["lines"]} == {"product", "service"}
    CatalogItem.objects.filter(pk=product["id"]).update(sale_price=Decimal("300.00"))
    assert Sale.objects.get(pk=result.json()["id"]).lines.get(kind="product").unit_price == Decimal("180.00")
    again = post_json(client, url, payload)
    assert again.status_code == 200
    assert Sale.objects.filter(company=company).count() == 1
    second = post_json(client, url, sale_payload(tenant_data["contact"]), key="sale-2")
    assert second.status_code == 201
    assert Sale.objects.filter(contact=tenant_data["contact"]).count() == 2


@pytest.mark.django_db
def test_order_and_opportunity_do_not_create_sale_and_order_cannot_duplicate(client, tenant_data):
    company = tenant_data["company"]
    client.force_login(tenant_data["user"])
    order = Order.objects.create(company=company, conversation=tenant_data["conversation"], status=Order.Status.APPROVED)
    won_url = reverse("api-conversation-action", args=[company.pk, tenant_data["conversation"].pk, "mark-won"])
    assert post_json(client, won_url, {}).status_code == 200
    assert Sale.objects.count() == 0
    url = reverse("api-sales", args=[company.pk])
    first = post_json(client, url, sale_payload(tenant_data["contact"], order=order))
    assert first.status_code == 201, first.content
    duplicate = post_json(client, url, sale_payload(tenant_data["contact"], order=order), key="sale-2")
    assert duplicate.status_code == 400
    assert Sale.objects.count() == 1


@pytest.mark.django_db
def test_sale_permissions_and_cross_tenant_relation(client, tenant_data):
    company = tenant_data["company"]
    url = reverse("api-sales", args=[company.pk])
    client.force_login(tenant_data["user"])
    foreign = post_json(client, url, sale_payload(tenant_data["contact_b"]))
    assert foreign.status_code == 400
    assert Sale.objects.count() == 0
    Membership.objects.filter(company=company, user=tenant_data["user"]).update(role="agent")
    assert client.get(url).status_code == 403
    assert post_json(client, url, sale_payload(tenant_data["contact"])).status_code == 403
    assert client.get(reverse("api-finance-dashboard", args=[company.pk])).status_code == 403


@pytest.mark.django_db
def test_ai_assistance_requires_sent_response_before_sale(client, tenant_data):
    company, conversation = tenant_data["company"], tenant_data["conversation"]
    client.force_login(tenant_data["user"])
    inbound = Message.objects.create(company=company, conversation=conversation, external_id="in-ai", direction="in", ai_status="completed")
    now = timezone.now()
    ai = Message.objects.create(
        company=company, conversation=conversation, external_id="out-ai", direction="out",
        response_to=inbound, status="sent", sent_at=now - timedelta(hours=2),
    )
    url = reverse("api-sales", args=[company.pk])
    result = post_json(client, url, sale_payload(tenant_data["contact"], conversation=conversation, sold_at=now))
    assert result.status_code == 201, result.content
    assert result.json()["attribution"] == "ai_assisted"
    assert result.json()["ai_evidence_message"] == ai.pk
    earlier = post_json(client, url, sale_payload(tenant_data["contact"], conversation=conversation, sold_at=now - timedelta(days=1)), key="sale-2")
    assert earlier.status_code == 201
    assert earlier.json()["attribution"] == "unknown"


@pytest.mark.django_db
def test_partial_payment_refund_and_fee_snapshot(client, tenant_data):
    company = tenant_data["company"]
    client.force_login(tenant_data["user"])
    sale = post_json(client, reverse("api-sales", args=[company.pk]), sale_payload(tenant_data["contact"])).json()
    method = PaymentMethod.objects.create(company=company, name="Crédito 1x", kind="credit", percent_fee=Decimal("2.000"))
    url = reverse("api-sale-payments", args=[company.pk, sale["id"]])
    receipt = post_json(client, url, {"kind": "receipt", "amount": "100.00", "occurred_at": timezone.now().isoformat(), "method_id": str(method.pk)}, key="receipt-1")
    assert receipt.status_code == 201, receipt.content
    assert receipt.json()["fee_amount"] == "2.00"
    method.percent_fee = Decimal("5.000")
    method.save(update_fields=["percent_fee"])
    assert SalePayment.objects.get(pk=receipt.json()["id"]).fee_percent == Decimal("2.000")
    refund = post_json(client, url, {"kind": "refund", "amount": "30.00", "occurred_at": timezone.now().isoformat(), "related_receipt_id": receipt.json()["id"]}, key="refund-1")
    assert refund.status_code == 201, refund.content
    detail = client.get(reverse("api-sale", args=[company.pk, sale["id"]])).json()
    assert detail["financials"]["received_net"] == "70.00"
    assert detail["financials"]["outstanding"] == "80.00"
    excessive = post_json(client, url, {"kind": "refund", "amount": "80.00", "occurred_at": timezone.now().isoformat(), "related_receipt_id": receipt.json()["id"]}, key="refund-2")
    assert excessive.status_code == 400


@pytest.mark.django_db
def test_dashboard_cancellation_and_csv_formula_safety(client, tenant_data):
    company = tenant_data["company"]
    client.force_login(tenant_data["user"])
    tenant_data["contact"].name = "=HYPERLINK(\"https://bad.example\")"
    tenant_data["contact"].save(update_fields=["name"])
    sale = post_json(client, reverse("api-sales", args=[company.pk]), sale_payload(tenant_data["contact"])).json()
    dashboard = client.get(reverse("api-finance-dashboard", args=[company.pk])).json()
    assert dashboard["confirmed_sales"] == 1
    assert dashboard["registered_revenue"] == "150.00"
    csv_result = client.get(reverse("api-sales-report", args=[company.pk]))
    assert csv_result.status_code == 200
    assert "'=HYPERLINK" in csv_result.content.decode()
    cancel = post_json(client, reverse("api-sale-cancel", args=[company.pk, sale["id"]]), {"reason": "Cliente desistiu"})
    assert cancel.status_code == 200
    dashboard = client.get(reverse("api-finance-dashboard", args=[company.pk])).json()
    assert dashboard["confirmed_sales"] == 0
    assert dashboard["cancelled_sales"] == 1


@pytest.mark.django_db
def test_complete_financial_snapshot_and_optional_profile(client, tenant_data):
    company = tenant_data["company"]
    client.force_login(tenant_data["user"])
    setting = client.patch(
        reverse("api-company", args=[company.pk]),
        json.dumps({"business_profile": "products", "estimated_tax_percent": "10.000"}),
        content_type="application/json",
    )
    assert setting.status_code == 200, setting.content
    catalog = post_json(client, reverse("api-catalog-items", args=[company.pk]), {
        "name": "Kit", "kind": "product", "price_mode": "fixed",
        "sale_price": "100.00", "unit_cost": "40.00",
    }).json()
    payload = sale_payload(tenant_data["contact"], lines=[{
        "catalog_item_id": catalog["id"], "quantity": "1", "unit_price": "100.00",
    }])
    payload["expenses"] = [{"name": "Entrega", "amount": "5.00"}]
    payload["expenses_complete"] = True
    sale = post_json(client, reverse("api-sales", args=[company.pk]), payload).json()
    method = PaymentMethod.objects.create(company=company, name="Crédito", kind="credit", percent_fee=Decimal("2.000"))
    payment = post_json(client, reverse("api-sale-payments", args=[company.pk, sale["id"]]), {
        "kind": "receipt", "amount": "100.00", "occurred_at": timezone.now().isoformat(), "method_id": str(method.pk),
    }, key="full-receipt")
    assert payment.status_code == 201, payment.content
    detail = client.get(reverse("api-sale", args=[company.pk, sale["id"]])).json()
    assert detail["financials"]["cost_data"] == "complete"
    assert detail["financials"]["known_costs"] == "57.00"
    assert detail["financials"]["estimated_result"] == "43.00"
    company.estimated_tax_percent = Decimal("25.000")
    company.save(update_fields=["estimated_tax_percent"])
    method.percent_fee = Decimal("9.000")
    method.save(update_fields=["percent_fee"])
    assert Sale.objects.get(pk=sale["id"]).estimated_tax_amount == Decimal("10.00")
    assert client.get(reverse("api-sale", args=[company.pk, sale["id"]])).json()["financials"]["estimated_result"] == "43.00"
    dashboard = client.get(reverse("api-finance-dashboard", args=[company.pk])).json()
    assert dashboard["payment_fees"] == "2.00"
    assert dashboard["estimated_taxes"] == "10.00"
    assert dashboard["estimated_result"] == "43.00"


@pytest.mark.django_db
def test_ai_correction_requires_evidence_inside_window(client, tenant_data):
    company, conversation = tenant_data["company"], tenant_data["conversation"]
    client.force_login(tenant_data["user"])
    inbound = Message.objects.create(company=company, conversation=conversation, external_id="in-old", direction="in", ai_status="completed")
    old = Message.objects.create(
        company=company, conversation=conversation, external_id="out-old", direction="out",
        response_to=inbound, status="sent", sent_at=timezone.now() - timedelta(days=40),
    )
    sale = post_json(client, reverse("api-sales", args=[company.pk]), sale_payload(tenant_data["contact"], conversation=conversation)).json()
    assert sale["attribution"] == "unknown"
    result = post_json(client, reverse("api-sale-attribution", args=[company.pk, sale["id"]]), {
        "attribution": "ai_assisted", "reason": "Revisão", "evidence_message_id": old.pk,
    })
    assert result.status_code == 400
    assert Sale.objects.get(pk=sale["id"]).attribution == "unknown"


@pytest.mark.django_db
def test_category_csv_allocates_sale_discount_and_payment_report_uses_payment_date(client, tenant_data):
    company = tenant_data["company"]
    client.force_login(tenant_data["user"])
    category_url = reverse("api-catalog-categories", args=[company.pk])
    category = post_json(client, category_url, {"name": "Serviços"}).json()
    item = post_json(client, reverse("api-catalog-items", args=[company.pk]), {
        "name": "Instalação", "kind": "service", "category": category["id"],
        "price_mode": "fixed", "sale_price": "80.00",
    }).json()
    payload = sale_payload(tenant_data["contact"], lines=[
        {"catalog_item_id": item["id"], "quantity": "1"},
        {"name": "Produto avulso", "kind": "product", "quantity": "1", "unit_price": "20.00"},
    ])
    payload["discount_amount"] = "10.00"
    sale = post_json(client, reverse("api-sales", args=[company.pk]), payload).json()
    assert sale["total"] == "90.00"
    report = client.get(reverse("api-sales-report", args=[company.pk]), {"group_by": "category"}).content.decode()
    assert "72.00" in report and "18.00" in report
    dashboard = client.get(reverse("api-finance-dashboard", args=[company.pk])).json()
    assert sum(Decimal(row["amount"]) for row in dashboard["by_category"]) == Decimal("90.00")
    filtered = client.get(reverse("api-finance-dashboard", args=[company.pk]), {"kind": "service"}).json()
    assert filtered["confirmed_sales"] == 1
    yesterday = timezone.now() - timedelta(days=1)
    # A receipt entered today remains in today's cash report even if the sale date was earlier.
    Sale.objects.filter(pk=sale["id"]).update(sold_at=yesterday)
    post_json(client, reverse("api-sale-payments", args=[company.pk, sale["id"]]), {
        "kind": "receipt", "amount": "90.00", "occurred_at": timezone.now().isoformat(),
    }, key="cash-report")
    today = timezone.localdate().isoformat()
    cash_report = client.get(reverse("api-sales-report", args=[company.pk]), {"group_by": "payments", "from": today, "to": today}).content.decode()
    assert "90.00" in cash_report
