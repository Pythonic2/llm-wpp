import json
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core import signing
from django.urls import reverse
from django.utils import timezone

from saas.billing import AsaasClient, BillingProviderError, number_billing_available, reconcile_checkout
from saas.billing_email import SALT, send_verification_email
from saas.services import send_whatsapp_text

from saas.models import BillingCheckout, BillingCustomer, BillingEvent, Company, Membership, PlanPriceRevision, PlanVersion, WhatsAppNumber


@pytest.fixture
def asaas_mock(settings):
    cache.clear()
    settings.ASAAS_API_KEY = "sandbox-test-token"
    settings.ASAAS_BASE_URL = "https://api-sandbox.asaas.com/v3"
    settings.ASAAS_WEBHOOK_AUTH_TOKEN = "webhook-test-token-with-more-than-32-characters"
    settings.GESTTECH_PUBLIC_URL = "https://crm.gestcloud.com.br"
    settings.GESTTECH_FROM_EMAIL = "noreply@example.com"
    settings.EMAIL_HOST_USER = "noreply@example.com"
    settings.EMAIL_HOST_PASSWORD = "test-only-password"
    calls = []
    customer = {"id": "cus_sandbox_test", "email": ""}

    def request(_self, method, path, *, json=None, params=None):
        calls.append((method, path, json, params))
        if method == "GET" and path == "/customers":
            return {"data": []}
        if method == "POST" and path == "/customers":
            customer["email"] = json.get("email", "")
            return customer.copy()
        if method == "GET" and path == "/customers/cus_sandbox_test":
            return customer.copy()
        if method == "PUT" and path == "/customers/cus_sandbox_test":
            customer["email"] = json["email"]
            return customer.copy()
        if method == "POST" and path == "/checkouts":
            return {"id": str(uuid.uuid4())}
        raise AssertionError((method, path))

    with patch("saas.billing.AsaasClient.request", request):
        yield calls


def signup_payload():
    return {
        "full_name": "Maria da Silva", "cpf_cnpj": "529.982.247-25", "mobile_phone": "(11) 99999-0000",
        "email": "maria@example.com", "password": "SenhaSegura123!", "company_name": "Ateliê da Maria",
        "postal_code": "01310-000", "address": "Avenida Paulista", "address_number": "100",
        "province": "Bela Vista", "city_name": "São Paulo", "state": "SP",
    }


def legacy_owner():
    user = get_user_model().objects.create_user("legacy-owner", password="test-password", email="")
    company = Company.objects.create(name="Empresa legada", slug="empresa-legada")
    Membership.objects.create(user=user, company=company, role=Membership.Role.OWNER)
    return user, company


def legacy_profile(user, *, asaas_customer_id=""):
    return BillingCustomer.objects.create(
        user=user, full_name="Cliente Legado", cpf_cnpj="52998224725", mobile_phone="11999990000",
        postal_code="01310000", address="Avenida Paulista", address_number="100",
        province="Bela Vista", city_name="São Paulo", state="SP",
        asaas_customer_id=asaas_customer_id,
    )


def post_json(client, url, payload, **headers):
    return client.post(url, json.dumps(payload), content_type="application/json", **headers)


def paid_event(checkout, event_id="evt_checkout_paid"):
    return {
        "id": event_id, "event": "CHECKOUT_PAID",
        "checkout": {"id": checkout.asaas_checkout_id, "status": "PAID", "externalReference": str(checkout.pk)},
    }


def webhook(client, payload, token="webhook-test-token-with-more-than-32-characters"):
    return post_json(client, reverse("asaas-webhook"), payload, HTTP_ASAAS_ACCESS_TOKEN=token)


def test_asaas_rejects_mismatched_key_and_api_environment(settings):
    settings.ASAAS_ALLOW_PRODUCTION = True
    settings.ASAAS_API_KEY = "$aact_prod_test-only"
    settings.ASAAS_BASE_URL = "https://api-sandbox.asaas.com/v3"
    with pytest.raises(BillingProviderError):
        AsaasClient()
    settings.ASAAS_API_KEY = "$aact_hmlg_test-only"
    settings.ASAAS_BASE_URL = "https://api.asaas.com/v3"
    with pytest.raises(BillingProviderError):
        AsaasClient()


@pytest.mark.django_db
def test_pending_sandbox_signup_restarts_in_production_without_new_company(client, asaas_mock, settings):
    payload = signup_payload()
    first = post_json(client, reverse("api-public-signup"), payload)
    assert first.status_code == 201, first.content
    old = BillingCheckout.objects.get(pk=first.json()["id"])
    old.checkout_url = "https://asaas.com/checkoutSession/show?id=" + old.asaas_checkout_id
    old.save(update_fields=["checkout_url"])
    status_url = reverse("api-public-checkout-status", args=[old.status_token])
    assert client.get(status_url).json()["checkout_url"].startswith("https://sandbox.asaas.com/")

    settings.ASAAS_API_KEY = "$aact_prod_test-only"
    settings.ASAAS_BASE_URL = "https://api.asaas.com/v3"
    settings.ASAAS_ALLOW_PRODUCTION = True
    assert client.get(status_url).json()["checkout_url"] == ""
    second = post_json(client, reverse("api-public-signup"), payload)
    assert second.status_code == 201, second.content
    assert second.json()["checkout_url"].startswith("https://asaas.com/checkoutSession/show?id=")
    new = BillingCheckout.objects.get(pk=second.json()["id"])
    old.refresh_from_db()
    assert old.status == BillingCheckout.Status.EXPIRED
    assert old.company_id == new.company_id
    assert new.asaas_environment == "production"
    assert new.user.billing_customer.asaas_environment == "production"
    assert Company.objects.count() == 1
    assert len([call for call in asaas_mock if call[:2] == ("POST", "/customers")]) == 2
    assert webhook(client, paid_event(old, "sandbox-event-after-switch")).status_code == 400
    assert new.company.billing_status == Company.BillingStatus.PENDING
    forged = paid_event(new, "missing-checkout-id")
    forged["checkout"].pop("id")
    assert webhook(client, forged).status_code == 400


def verify_email(client, checkout):
    token = signing.dumps({"user_id": checkout.user_id, "email": checkout.user.email}, salt=SALT)
    assert post_json(client, reverse("api-public-verify-email"), {"token": token}).status_code == 200


@pytest.mark.django_db
def test_signup_requires_paid_webhook_and_is_idempotent(client, asaas_mock):
    result = post_json(client, reverse("api-public-signup"), signup_payload())
    assert result.status_code == 201, result.content
    data = result.json()
    assert data["amount"] == "249.90"
    assert data["checkout_url"].startswith("https://sandbox.asaas.com/checkoutSession/show?id=")
    checkout = BillingCheckout.objects.get(pk=data["id"])
    assert checkout.company.billing_status == Company.BillingStatus.PENDING
    assert not checkout.user.is_active
    assert BillingCustomer.objects.get(user=checkout.user).asaas_customer_id == "cus_sandbox_test"
    assert BillingCustomer.objects.get(user=checkout.user).cpf_cnpj == ""
    customer_request = next(call for call in asaas_mock if call[1] == "/customers" and call[0] == "POST")
    assert customer_request[2]["cpfCnpj"] == "52998224725"
    checkout_request = next(call for call in asaas_mock if call[1] == "/checkouts")
    assert checkout_request[2]["billingTypes"] == ["CREDIT_CARD"]
    assert checkout_request[2]["chargeTypes"] == ["RECURRENT"]
    assert checkout_request[2]["items"][0]["value"] == 249.9
    assert "sandbox-test-token" not in result.content.decode()
    status_url = reverse("api-public-checkout-status", args=[data["status_token"]])
    assert client.get(status_url).json()["can_login"] is False
    assert post_json(client, reverse("api-login"), {"username": checkout.user.email, "password": signup_payload()["password"]}).status_code == 400
    assert webhook(client, paid_event(checkout), token="wrong").status_code == 403
    assert checkout.company.billing_status == Company.BillingStatus.PENDING
    verify_email(client, checkout)
    checkout.user.refresh_from_db()
    assert not checkout.user.is_active
    assert webhook(client, paid_event(checkout)).status_code == 200
    assert webhook(client, paid_event(checkout)).status_code == 200
    checkout.refresh_from_db()
    assert checkout.status == BillingCheckout.Status.PAID
    assert BillingEvent.objects.count() == 1
    checkout.company.refresh_from_db()
    checkout.user.refresh_from_db()
    assert checkout.company.billing_status == Company.BillingStatus.ACTIVE
    assert checkout.user.is_active
    assert client.get(status_url).json()["can_login"] is True
    assert post_json(client, reverse("api-login"), {"username": checkout.user.email, "password": signup_payload()["password"]}).status_code == 200


@pytest.mark.django_db
def test_payment_alone_does_not_activate_unverified_email(client, asaas_mock, settings, mailoutbox):
    first = post_json(client, reverse("api-public-signup"), signup_payload()).json()
    checkout = BillingCheckout.objects.get(pk=first["id"])
    settings.GESTTECH_FROM_EMAIL = "noreply@example.com"
    send_verification_email(checkout.user.billing_customer.pk)
    assert len(mailoutbox) == 1
    assert checkout.user.email in mailoutbox[0].to
    assert webhook(client, paid_event(checkout)).status_code == 200
    checkout.user.refresh_from_db()
    assert not checkout.user.is_active
    assert client.get(reverse("api-public-checkout-status", args=[first["status_token"]])).json()["can_login"] is False
    assert post_json(client, reverse("api-public-verify-email"), {"token": "invalid"}).status_code == 400
    token = signing.dumps({"user_id": checkout.user_id, "email": checkout.user.email}, salt=SALT)
    assert post_json(client, reverse("api-public-verify-email"), {"token": token}).status_code == 200
    checkout.user.refresh_from_db()
    assert checkout.user.is_active


@pytest.mark.django_db
def test_public_signup_is_closed_when_smtp_is_missing(client, asaas_mock, settings):
    settings.DEBUG = False
    settings.EMAIL_HOST_PASSWORD = ""
    result = post_json(client, reverse("api-public-signup"), signup_payload())
    assert result.status_code == 503
    assert not BillingCheckout.objects.exists()


@pytest.mark.django_db
def test_invalid_document_and_duplicate_email_are_rejected(client, asaas_mock):
    bad = signup_payload()
    bad["cpf_cnpj"] = "111.111.111-11"
    assert post_json(client, reverse("api-public-signup"), bad).status_code == 400
    assert not BillingCheckout.objects.exists()
    assert post_json(client, reverse("api-public-signup"), signup_payload()).status_code == 201
    assert post_json(client, reverse("api-public-signup"), signup_payload()).status_code == 200
    duplicate = signup_payload()
    duplicate["password"] = "OutraSenhaSegura123!"
    assert post_json(client, reverse("api-public-signup"), duplicate).status_code == 400
    assert BillingCheckout.objects.count() == 1


@pytest.mark.django_db
def test_paid_extra_company_is_not_visible_until_webhook(client, asaas_mock):
    first = post_json(client, reverse("api-public-signup"), signup_payload()).json()
    checkout = BillingCheckout.objects.get(pk=first["id"])
    verify_email(client, checkout)
    webhook(client, paid_event(checkout))
    client.force_login(checkout.user)
    extra = post_json(client, reverse("api-billing-extra-company"), {"company_name": "Segunda Empresa"}, HTTP_IDEMPOTENCY_KEY="extra-company-1")
    assert extra.status_code == 201, extra.content
    second = BillingCheckout.objects.get(pk=extra.json()["id"])
    assert second.amount == Decimal("199.90")
    companies = client.get(reverse("api-companies")).json()
    assert len(companies) == 1
    assert client.get(reverse("api-company", args=[second.company_id])).status_code == 404
    duplicate = post_json(client, reverse("api-billing-extra-company"), {"company_name": "Segunda Empresa"}, HTTP_IDEMPOTENCY_KEY="extra-company-1")
    assert duplicate.status_code == 200
    assert BillingCheckout.objects.filter(kind=BillingCheckout.Kind.EXTRA_COMPANY).count() == 1
    webhook(client, paid_event(second, "evt_second_paid"))
    companies = client.get(reverse("api-companies")).json()
    assert len(companies) == 2
    assert {item["name"] for item in companies} == {"Ateliê da Maria", "Segunda Empresa"}


@pytest.mark.django_db
def test_legacy_extra_company_requires_billing_email_before_creating_company(client, asaas_mock):
    user, _ = legacy_owner()
    client.force_login(user)
    profile = signup_payload()
    profile = {key: profile[key] for key in (
        "full_name", "cpf_cnpj", "mobile_phone", "postal_code", "address", "address_number",
        "province", "city_name", "state",
    )}
    before = Company.objects.count()
    missing = post_json(client, reverse("api-billing-extra-company"), {
        "company_name": "Empresa extra", "billing_profile": profile,
    }, HTTP_IDEMPOTENCY_KEY="legacy-extra-missing")
    assert missing.status_code == 400
    assert Company.objects.count() == before
    assert not BillingCustomer.objects.filter(user=user).exists()
    assert client.get(reverse("api-billing-profile-status")).json()["email_required"] is True
    created = post_json(client, reverse("api-billing-extra-company"), {
        "company_name": "Empresa extra", "billing_profile": profile,
        "billing_email": "legacy@example.com",
    }, HTTP_IDEMPOTENCY_KEY="legacy-extra-valid")
    assert created.status_code == 201, created.content
    user.refresh_from_db()
    assert user.email == "legacy@example.com"
    assert Company.objects.count() == before + 1
    assert client.get(reverse("api-billing-profile-status")).json()["email_required"] is False
    customer_create = next(call for call in asaas_mock if call[:2] == ("POST", "/customers"))
    assert customer_create[2]["email"] == "legacy@example.com"


@pytest.mark.django_db
def test_review_from_missing_asaas_email_reuses_checkout_and_company(client, asaas_mock):
    user, _ = legacy_owner()
    profile = legacy_profile(user, asaas_customer_id="cus_sandbox_test")
    company = Company.objects.create(name="Empresa extra", slug="empresa-extra")
    Membership.objects.create(user=user, company=company, role=Membership.Role.OWNER)
    checkout = BillingCheckout.objects.create(
        user=user, company=company, kind=BillingCheckout.Kind.EXTRA_COMPANY,
        status=BillingCheckout.Status.REVIEW, amount=Decimal("199.90"),
        request_key="old-key", request_hash="old-hash", checkout_started_at=timezone.now(),
    )
    before = Company.objects.count()
    client.force_login(user)
    result = post_json(client, reverse("api-billing-extra-company"), {
        "company_name": "Empresa extra", "billing_email": "legacy@example.com",
    }, HTTP_IDEMPOTENCY_KEY="recovered-key")
    assert result.status_code == 201, result.content
    assert result.json()["id"] == str(checkout.pk)
    assert Company.objects.count() == before
    checkout.refresh_from_db()
    profile.refresh_from_db()
    assert checkout.status == BillingCheckout.Status.PENDING
    assert checkout.asaas_checkout_id
    assert profile.asaas_customer_id == "cus_sandbox_test"
    assert ("PUT", "/customers/cus_sandbox_test") in [(method, path) for method, path, _, _ in asaas_mock]
    repeated = post_json(client, reverse("api-billing-extra-company"), {
        "company_name": "Empresa extra", "billing_email": "legacy@example.com",
    }, HTTP_IDEMPOTENCY_KEY="recovered-key")
    assert repeated.status_code == 200
    assert Company.objects.count() == before


@pytest.mark.django_db
def test_review_with_asaas_email_stays_blocked_for_manual_reconciliation(client, asaas_mock):
    user, _ = legacy_owner()
    legacy_profile(user, asaas_customer_id="cus_sandbox_test")
    company = Company.objects.create(name="Empresa extra", slug="empresa-extra")
    Membership.objects.create(user=user, company=company, role=Membership.Role.OWNER)
    BillingCheckout.objects.create(
        user=user, company=company, kind=BillingCheckout.Kind.EXTRA_COMPANY,
        status=BillingCheckout.Status.REVIEW, amount=Decimal("199.90"),
        request_key="old-key", request_hash="old-hash", checkout_started_at=timezone.now(),
    )
    client.force_login(user)
    def known_email(_self, method, path, *, json=None, params=None):
        assert (method, path) == ("GET", "/customers/cus_sandbox_test")
        return {"id": "cus_sandbox_test", "email": "existing@example.com"}
    with patch("saas.billing.AsaasClient.request", known_email):
        response = post_json(client, reverse("api-billing-extra-company"), {
            "company_name": "Empresa extra", "billing_email": "legacy@example.com",
        }, HTTP_IDEMPOTENCY_KEY="new-key")
    assert response.status_code == 400
    assert BillingCheckout.objects.filter(kind=BillingCheckout.Kind.EXTRA_COMPANY).count() == 1
    user.refresh_from_db()
    assert user.email == ""


@pytest.mark.django_db
def test_number_limit_requires_paid_slot_and_is_company_scoped(client, asaas_mock):
    first = post_json(client, reverse("api-public-signup"), signup_payload()).json()
    checkout = BillingCheckout.objects.get(pk=first["id"])
    verify_email(client, checkout)
    webhook(client, paid_event(checkout))
    company = checkout.company
    client.force_login(checkout.user)
    url = reverse("api-whatsapp-numbers", args=[company.pk])
    for index in (1, 2):
        result = post_json(client, url, {"phone_number_id": f"meta-{index}", "display_phone_number": f"+551199990000{index}"})
        assert result.status_code == 201, result.content
    blocked = post_json(client, url, {"phone_number_id": "meta-3", "display_phone_number": "+5511999900003"})
    assert blocked.status_code == 400
    quota = client.get(reverse("api-billing-quota", args=[company.pk])).json()
    assert quota["number_capacity"] == 2 and quota["active_numbers"] == 2
    addon = post_json(client, reverse("api-billing-extra-number", args=[company.pk]), {}, HTTP_IDEMPOTENCY_KEY="extra-number-1")
    assert addon.status_code == 201, addon.content
    slot = BillingCheckout.objects.get(pk=addon.json()["id"])
    assert slot.amount == Decimal("30.00")
    assert post_json(client, url, {"phone_number_id": "meta-3", "display_phone_number": "+5511999900003"}).status_code == 400
    webhook(client, paid_event(slot, "evt_number_paid"))
    assert post_json(client, url, {"phone_number_id": "meta-3", "display_phone_number": "+5511999900003"}).status_code == 201
    assert client.get(reverse("api-billing-quota", args=[company.pk])).json()["number_capacity"] == 3
    third = WhatsAppNumber.objects.get(phone_number_id="meta-3")
    slot.overdue_since = timezone.now() - timedelta(days=6)
    slot.save(update_fields=["overdue_since"])
    assert number_billing_available(third)
    slot.overdue_since = timezone.now() - timedelta(days=8)
    slot.save(update_fields=["overdue_since"])
    assert not number_billing_available(third)
    assert number_billing_available(WhatsAppNumber.objects.get(phone_number_id="meta-1"))
    with pytest.raises(ValueError, match="vaga"):
        send_whatsapp_text(third, "5511999999999", "teste")
    assert client.get(reverse("api-billing-quota", args=[company.pk])).json()["number_capacity"] == 2


@pytest.mark.django_db
def test_existing_plan_keeps_previous_additional_number_price(client, tenant_data):
    company = tenant_data["company"]
    company.plan, _ = PlanVersion.objects.get_or_create(
        code="basic", version=1,
        defaults={"name": "Básico anterior", "included_business_units": 2,
                  "included_whatsapp_numbers": 2,
                  "additional_whatsapp_monthly_price": Decimal("49.90")},
    )
    company.save(update_fields=["plan"])
    client.force_login(tenant_data["user"])
    quota = client.get(reverse("api-billing-quota", args=[company.pk])).json()
    assert quota["extra_number_price"] == "49.90"
    assert quota["included_numbers"] == 2


@pytest.mark.django_db
def test_admin_price_change_only_affects_new_checkouts(client, asaas_mock):
    owner, company = legacy_owner()
    legacy_profile(owner)
    old = BillingCheckout.objects.create(
        user=owner, company=company, kind=BillingCheckout.Kind.EXTRA_NUMBER,
        status=BillingCheckout.Status.PAID, amount=Decimal("30.00"),
        asaas_subscription_id="sub_existing_price", asaas_environment="sandbox",
    )
    plan = company.plan
    admin = get_user_model().objects.create_superuser("price-admin", password="test")
    client.force_login(admin)
    response = client.post(reverse("admin:saas_planversion_change", args=[plan.pk]), {
        "name": plan.name,
        "included_business_units": 2,
        "included_whatsapp_numbers": 2,
        "first_company_monthly_price": "259.90",
        "extra_company_monthly_price": "209.90",
        "additional_whatsapp_monthly_price": "37.00",
        "reason": "Nova tabela comercial para futuras contratações.",
    })
    assert response.status_code == 302, response.content
    revision = PlanPriceRevision.objects.get(plan=plan)
    assert revision.actor == admin
    assert revision.previous_prices["additional_whatsapp_monthly_price"] == "30.00"
    old.refresh_from_db()
    assert old.amount == Decimal("30.00")
    assert not any(path.startswith("/subscriptions/") for _, path, _, _ in asaas_mock)

    prices = client.get(reverse("api-public-prices")).json()
    assert prices["first_company_price"] == "259.90"
    assert prices["extra_company_price"] == "209.90"
    assert prices["extra_number_price"] == "37.00"

    client.force_login(owner)
    checkout = post_json(
        client, reverse("api-billing-extra-number", args=[company.pk]),
        {"billing_email": "owner@example.com"}, HTTP_IDEMPOTENCY_KEY="new-number-price",
    )
    assert checkout.status_code == 201, checkout.content
    assert BillingCheckout.objects.get(pk=checkout.json()["id"]).amount == Decimal("37.00")
    old.refresh_from_db()
    assert old.amount == Decimal("30.00")


@pytest.mark.django_db
def test_new_company_checkouts_use_current_plan_prices(client, asaas_mock):
    seed = Company.objects.create(name="Seed", slug="price-seed")
    plan = seed.plan
    plan.first_company_monthly_price = Decimal("259.90")
    plan.extra_company_monthly_price = Decimal("209.90")
    plan.save(update_fields=["first_company_monthly_price", "extra_company_monthly_price"])
    first = post_json(client, reverse("api-public-signup"), signup_payload())
    assert first.status_code == 201, first.content
    assert BillingCheckout.objects.get(pk=first.json()["id"]).amount == Decimal("259.90")


@pytest.mark.django_db
def test_extra_company_checkout_uses_current_plan_price(client, asaas_mock):
    owner, company = legacy_owner()
    owner.email = "owner@example.com"
    owner.save(update_fields=["email"])
    legacy_profile(owner)
    plan = company.plan
    plan.extra_company_monthly_price = Decimal("209.90")
    plan.save(update_fields=["extra_company_monthly_price"])
    client.force_login(owner)
    result = post_json(client, reverse("api-billing-extra-company"), {"company_name": "Nova empresa"}, HTTP_IDEMPOTENCY_KEY="new-company-price")
    assert result.status_code == 201, result.content
    assert BillingCheckout.objects.get(pk=result.json()["id"]).amount == Decimal("209.90")


@pytest.mark.django_db
def test_only_owner_can_buy_for_company(client, tenant_data, asaas_mock):
    client.force_login(tenant_data["user"])
    Membership.objects.filter(company=tenant_data["company"], user=tenant_data["user"]).update(role="admin")
    assert post_json(client, reverse("api-billing-extra-company"), {"company_name": "Outra"}, HTTP_IDEMPOTENCY_KEY="company-admin").status_code == 403
    assert post_json(client, reverse("api-billing-extra-number", args=[tenant_data["company"].pk]), {}, HTTP_IDEMPOTENCY_KEY="number-admin").status_code == 403
    assert not BillingCheckout.objects.exists()


@pytest.mark.django_db
def test_overdue_company_has_seven_day_grace_and_paid_event_restores_access(client, asaas_mock):
    first = post_json(client, reverse("api-public-signup"), signup_payload()).json()
    checkout = BillingCheckout.objects.get(pk=first["id"])
    verify_email(client, checkout)
    webhook(client, paid_event(checkout))
    client.force_login(checkout.user)
    checkout.asaas_subscription_id = "sub_test"
    checkout.overdue_since = timezone.now() - timedelta(days=6)
    checkout.save(update_fields=["asaas_subscription_id", "overdue_since"])
    assert len(client.get(reverse("api-companies")).json()) == 1
    checkout.overdue_since = timezone.now() - timedelta(days=8)
    checkout.save(update_fields=["overdue_since"])
    assert client.get(reverse("api-companies")).json() == []
    assert client.get(reverse("api-company", args=[checkout.company_id])).status_code == 404
    renewal = {"id": "evt_renewal_paid", "event": "PAYMENT_CONFIRMED", "payment": {
        "id": "pay_renewal", "subscription": "sub_test", "dueDate": timezone.localdate().isoformat(),
    }}
    assert webhook(client, renewal).status_code == 200
    checkout.refresh_from_db()
    assert checkout.overdue_since is None
    assert len(client.get(reverse("api-companies")).json()) == 1


@pytest.mark.django_db
def test_reconciliation_maps_subscription_and_overdue_state(client, asaas_mock):
    first = post_json(client, reverse("api-public-signup"), signup_payload()).json()
    checkout = BillingCheckout.objects.get(pk=first["id"])
    verify_email(client, checkout)
    webhook(client, paid_event(checkout))
    old_due = (timezone.localdate() - timedelta(days=8)).isoformat()
    subscription_status = {"value": "ACTIVE"}

    def provider(_self, method, path, *, json=None, params=None):
        assert method == "GET"
        if path == "/subscriptions/sub_reconciled":
            return {"customer": "cus_sandbox_test", "status": subscription_status["value"], "deleted": False}
        assert path == "/payments"
        if "checkoutSession" in params:
            return {"data": [{"subscription": "sub_reconciled", "customer": "cus_sandbox_test", "value": 249.90}]}
        assert params["subscription"] == "sub_reconciled"
        return {"data": [{"id": "pay_late", "customer": "cus_sandbox_test", "value": 249.90,
                          "subscription": "sub_reconciled", "dueDate": old_due, "status": "OVERDUE"}], "hasMore": False}

    with patch("saas.billing.AsaasClient.request", provider):
        assert reconcile_checkout(checkout.pk)
    checkout.refresh_from_db()
    assert checkout.asaas_subscription_id == "sub_reconciled"
    assert checkout.overdue_since is not None
    client.force_login(checkout.user)
    assert client.get(reverse("api-companies")).json() == []
    subscription_status["value"] = "INACTIVE"
    with patch("saas.billing.AsaasClient.request", provider):
        assert reconcile_checkout(checkout.pk)
    checkout.refresh_from_db()
    assert checkout.status == BillingCheckout.Status.REVOKED


@pytest.mark.django_db
def test_webhook_rejects_mismatched_customer_and_checkout(client, asaas_mock):
    first = post_json(client, reverse("api-public-signup"), signup_payload()).json()
    checkout = BillingCheckout.objects.get(pk=first["id"])
    forged = paid_event(checkout)
    forged["checkout"]["customer"] = "cus_other"
    assert webhook(client, forged).status_code == 400
    assert not checkout.user.is_active
    forged = paid_event(checkout, "evt_wrong_checkout")
    forged["checkout"]["id"] = str(uuid.uuid4())
    assert webhook(client, forged).status_code == 400
    checkout.refresh_from_db()
    assert checkout.status == BillingCheckout.Status.PENDING


@pytest.mark.django_db
def test_refund_revokes_company_immediately(client, asaas_mock):
    first = post_json(client, reverse("api-public-signup"), signup_payload()).json()
    checkout = BillingCheckout.objects.get(pk=first["id"])
    verify_email(client, checkout)
    webhook(client, paid_event(checkout))
    checkout.asaas_subscription_id = "sub_refund"
    checkout.last_payment_id = "pay_refund"
    checkout.save(update_fields=["asaas_subscription_id", "last_payment_id"])
    client.force_login(checkout.user)
    refund = {"id": "evt_refund", "event": "PAYMENT_REFUNDED", "payment": {
        "id": "pay_refund", "subscription": "sub_refund", "customer": "cus_sandbox_test",
    }}
    assert webhook(client, refund).status_code == 200
    checkout.refresh_from_db()
    assert checkout.status == BillingCheckout.Status.REVOKED
    assert client.get(reverse("api-companies")).json() == []
