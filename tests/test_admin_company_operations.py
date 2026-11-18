from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from saas.billing import accessible_companies, asaas_environment, company_billing_available, number_billing_available
from saas.models import BillingCheckout, CatalogCategory, CatalogItem, Company, PlanPriceRevision, Sale


pytestmark = pytest.mark.django_db


def admin_action(client, superuser, action, company):
    client.force_login(superuser)
    return client.post(
        reverse("admin:saas_company_changelist"),
        {"action": action, "_selected_action": [str(company.pk)]},
    )


@pytest.mark.parametrize("days", [7, 14])
def test_trial_grant_expires_without_changing_checkout(client, tenant_data, days):
    company = tenant_data["company"]
    company.billing_status = Company.BillingStatus.PENDING
    company.save(update_fields=["billing_status"])
    checkout = BillingCheckout.objects.create(
        company=company, user=tenant_data["user"],
        kind=BillingCheckout.Kind.FIRST_COMPANY,
        status=BillingCheckout.Status.PENDING, amount=Decimal("249.90"),
    )
    superuser = get_user_model().objects.create_superuser("super", password="test")
    assert not company_billing_available(company)
    before = timezone.now()
    response = admin_action(client, superuser, f"grant_trial_{days}", company)
    assert response.status_code == 302
    company.refresh_from_db()
    assert before + timedelta(days=days) <= company.trial_expires_at <= timezone.now() + timedelta(days=days)
    assert company_billing_available(company)
    assert company in accessible_companies()
    assert number_billing_available(tenant_data["number"])
    client.force_login(tenant_data["user"])
    assert len(client.get(reverse("api-companies")).json()) == 1
    assert client.get(reverse("api-company", args=[company.pk])).status_code == 200
    checkout.refresh_from_db()
    assert checkout.status == BillingCheckout.Status.PENDING
    assert LogEntry.objects.filter(object_id=str(company.pk), user=superuser).exists()

    company.trial_expires_at = timezone.now() - timedelta(seconds=1)
    company.save(update_fields=["trial_expires_at"])
    assert not company_billing_available(company)
    assert company not in accessible_companies()
    assert not number_billing_available(tenant_data["number"])
    client.force_login(tenant_data["user"])
    assert client.get(reverse("api-companies")).json() == []
    assert client.get(reverse("api-company", args=[company.pk])).status_code == 404


def test_archive_preserves_sales_and_checkout_and_restore_rechecks_access(client, tenant_data):
    company = tenant_data["company"]
    checkout = BillingCheckout.objects.create(
        company=company, user=tenant_data["user"],
        kind=BillingCheckout.Kind.FIRST_COMPANY,
        status=BillingCheckout.Status.PAID, amount=Decimal("249.90"),
        asaas_environment=asaas_environment(),
    )
    sale = Sale.objects.create(
        company=company, contact=tenant_data["contact"], sold_at=timezone.now(),
        subtotal=Decimal("10.00"), total=Decimal("10.00"),
        idempotency_key="admin-archive-test", payload_hash="test",
    )
    superuser = get_user_model().objects.create_superuser("super", password="test")
    assert admin_action(client, superuser, "archive_companies", company).status_code == 302
    company.refresh_from_db()
    assert company.archived_at is not None
    assert not company_billing_available(company)
    assert company not in accessible_companies()
    assert not number_billing_available(tenant_data["number"])
    assert Sale.objects.filter(pk=sale.pk).exists()
    assert BillingCheckout.objects.filter(pk=checkout.pk, status=BillingCheckout.Status.PAID).exists()
    company.billing_status = Company.BillingStatus.ACTIVE
    company.save(update_fields=["billing_status"])
    assert not company_billing_available(company)
    assert admin_action(client, superuser, "restore_companies", company).status_code == 302
    company.refresh_from_db()
    assert company.archived_at is None
    assert company_billing_available(company)


def test_trial_grant_converts_legacy_company_to_time_limited_access(client, tenant_data):
    company = tenant_data["company"]
    superuser = get_user_model().objects.create_superuser("super", password="test")
    assert admin_action(client, superuser, "grant_trial_7", company).status_code == 302
    company.refresh_from_db()
    assert company.billing_status == Company.BillingStatus.PENDING
    assert company_billing_available(company)
    company.trial_expires_at = timezone.now() - timedelta(seconds=1)
    company.save(update_fields=["trial_expires_at"])
    assert not company_billing_available(company)
    assert company not in accessible_companies()


def test_staff_cannot_grant_trial_or_archive_with_forged_admin_post(client, tenant_data):
    company = tenant_data["company"]
    staff = tenant_data["user"]
    staff.is_staff = True
    staff.save(update_fields=["is_staff"])
    staff.user_permissions.add(Permission.objects.get(codename="change_company"))
    staff.user_permissions.add(Permission.objects.get(codename="view_company"))
    staff.user_permissions.add(Permission.objects.get(codename="change_membership"))
    staff.user_permissions.add(Permission.objects.get(codename="change_whatsappnumber"))
    client.force_login(staff)
    url = reverse("admin:saas_company_changelist")
    assert client.get(url).status_code == 200
    for action in ("grant_trial_7", "grant_trial_14", "archive_companies"):
        client.post(url, {"action": action, "_selected_action": [str(company.pk)]})
    company.refresh_from_db()
    assert company.trial_expires_at is None
    assert company.archived_at is None
    assert client.get(reverse("admin:saas_company_add")).status_code == 403
    assert client.get(reverse("admin:saas_billingcheckout_changelist")).status_code == 403
    membership = tenant_data["company"].memberships.get(user=staff)
    assert client.post(reverse("admin:saas_membership_change", args=[membership.pk]), {"role": "owner"}).status_code == 403
    assert client.post(reverse("admin:saas_whatsappnumber_change", args=[tenant_data["number"].pk]), {"is_active": "on"}).status_code == 403
    membership.refresh_from_db()
    assert membership.role == "admin"


def test_superuser_can_inspect_billing_history_without_editing_it(client, tenant_data):
    checkout = BillingCheckout.objects.create(
        company=tenant_data["company"], user=tenant_data["user"],
        kind=BillingCheckout.Kind.FIRST_COMPANY, amount=Decimal("249.90"),
    )
    superuser = get_user_model().objects.create_superuser("super", password="test")
    client.force_login(superuser)
    assert client.get(reverse("admin:saas_billingcheckout_changelist")).status_code == 200
    assert client.get(reverse("admin:saas_billingcheckout_change", args=[checkout.pk])).status_code == 200
    assert client.post(reverse("admin:saas_billingcheckout_change", args=[checkout.pk]), {"status": "paid"}).status_code == 403
    checkout.refresh_from_db()
    assert checkout.status == BillingCheckout.Status.PENDING


def test_manual_catalog_item_rejects_cross_company_relations(tenant_data):
    from saas.admin import CatalogItemAdmin

    category = CatalogCategory.objects.create(company=tenant_data["company_b"], name="Outra")
    item = CatalogItem(company=tenant_data["company"], name="Produto", kind="product")
    form = CatalogItemAdmin.Form(data={
        "company": str(tenant_data["company"].pk),
        "category": category.pk,
        "name": "Produto", "kind": "product", "price_mode": "quote", "unit": "unidade",
    }, instance=item)
    assert not form.is_valid()
    assert "Categoria pertence a outra empresa" in str(form.errors)


def test_plan_price_requires_reason_and_superuser(client, tenant_data):
    plan = tenant_data["company"].plan
    payload = {
        "name": plan.name,
        "included_business_units": 2,
        "included_whatsapp_numbers": 2,
        "first_company_monthly_price": "249.90",
        "extra_company_monthly_price": "199.90",
        "additional_whatsapp_monthly_price": "35.00",
        "reason": "",
    }
    staff = tenant_data["user"]
    staff.is_staff = True
    staff.save(update_fields=["is_staff"])
    staff.user_permissions.add(Permission.objects.get(codename="change_planversion"))
    client.force_login(staff)
    url = reverse("admin:saas_planversion_change", args=[plan.pk])
    assert client.post(url, payload).status_code == 403

    superuser = get_user_model().objects.create_superuser("price-super", password="test")
    client.force_login(superuser)
    response = client.post(url, payload)
    assert response.status_code == 200
    assert "Informe um motivo" in response.content.decode()
    plan.refresh_from_db()
    assert plan.additional_whatsapp_monthly_price == Decimal("30.00")
    assert not PlanPriceRevision.objects.exists()
