"""Gest Tech subscriptions. Commercial sales made by tenants never enter this module."""

import uuid
from datetime import date, datetime, timedelta, time
from decimal import Decimal
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import BillingCheckout, BillingCustomer, BillingEvent, Company, WhatsAppNumber

PAYMENT_GRACE = timedelta(days=7)


class BillingProviderError(Exception):
    pass


def asaas_environment():
    return "production" if settings.ASAAS_BASE_URL.rstrip("/") == "https://api.asaas.com/v3" else "sandbox"


def checkout_link(checkout):
    if not checkout.asaas_checkout_id:
        return ""
    host = "https://asaas.com" if checkout.asaas_environment == "production" else "https://sandbox.asaas.com"
    return f"{host}/checkoutSession/show?id={checkout.asaas_checkout_id}"


class AsaasClient:
    def __init__(self):
        self.key = settings.ASAAS_API_KEY
        self.base = settings.ASAAS_BASE_URL.rstrip("/")
        if not self.key or self.base not in {
            "https://api-sandbox.asaas.com/v3", "https://api.asaas.com/v3",
        }:
            raise BillingProviderError("Integração Asaas não configurada.")
        if self.base == "https://api.asaas.com/v3" and not settings.ASAAS_ALLOW_PRODUCTION:
            raise BillingProviderError("Ambiente Asaas de produção não autorizado.")
        if self.base == "https://api.asaas.com/v3" and not self.key.startswith("$aact_prod_"):
            raise BillingProviderError("Chave Asaas incompatível com produção.")
        if self.base == "https://api-sandbox.asaas.com/v3" and self.key.startswith("$aact_prod_"):
            raise BillingProviderError("Chave Asaas incompatível com sandbox.")

    def request(self, method, path, *, json=None, params=None):
        try:
            response = httpx.request(
                method, f"{self.base}{path}", json=json, params=params, timeout=12,
                headers={"access_token": self.key, "User-Agent": "GestTechCRM/1.0", "Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Resposta inválida")
            return data
        except (httpx.HTTPError, ValueError) as exc:
            # A resposta pode conter dados pessoais ou detalhes da chave: nunca propagá-la ao cliente.
            raise BillingProviderError("Não foi possível confirmar a operação no Asaas.") from exc


def ensure_customer(profile):
    """Reuse the Asaas customer by local ID; look up externalReference before POST retries."""
    with transaction.atomic():
        profile = BillingCustomer.objects.select_for_update().get(pk=profile.pk)
        if not profile.user.email:
            raise BillingProviderError("Informe um e-mail de cobrança antes de continuar.")
        if profile.asaas_environment != asaas_environment():
            raise BillingProviderError("Dados de cobrança pertencem a outro ambiente Asaas.")
        if profile.asaas_customer_id:
            client = AsaasClient()
            customer = client.request("GET", f"/customers/{profile.asaas_customer_id}")
            if str(customer.get("email") or "").strip().lower() != profile.user.email.lower():
                updated = client.request("PUT", f"/customers/{profile.asaas_customer_id}", json={"email": profile.user.email})
                if str(updated.get("email") or "").strip().lower() != profile.user.email.lower():
                    raise BillingProviderError("E-mail de cobrança não confirmado no Asaas.")
            return profile.asaas_customer_id
        client = AsaasClient()
        reference = f"gesttech-user-{profile.user_id}"
        existing = client.request("GET", "/customers", params={"externalReference": reference, "limit": 2}).get("data", [])
        if existing:
            matched = next((item for item in existing if item.get("externalReference") == reference), None)
            if matched is None:
                raise BillingProviderError("Cliente Asaas não conciliado.")
            customer_id = matched.get("id")
        else:
            customer_id = client.request("POST", "/customers", json={
                "name": profile.full_name, "cpfCnpj": profile.cpf_cnpj,
                "email": profile.user.email, "mobilePhone": profile.mobile_phone,
                "postalCode": profile.postal_code, "address": profile.address,
                "addressNumber": profile.address_number, "complement": profile.complement,
                "province": profile.province, "externalReference": reference,
            }).get("id")
        if not customer_id or not str(customer_id).startswith("cus_"):
            raise BillingProviderError("Cliente Asaas não confirmado.")
        profile.asaas_customer_id = customer_id
        # The provider owns the billing address and document after registration.
        profile.cpf_cnpj = ""
        profile.postal_code = ""
        profile.address = ""
        profile.address_number = ""
        profile.complement = ""
        profile.province = ""
        profile.city_name = ""
        profile.state = ""
        profile.save(update_fields=[
            "asaas_customer_id", "cpf_cnpj", "postal_code", "address", "address_number",
            "complement", "province", "city_name", "state", "updated_at",
        ])
        return customer_id


def checkout_payload(checkout, customer_id):
    base = settings.GESTTECH_PUBLIC_URL.rstrip("/")
    if not base.startswith("https://") and not settings.DEBUG:
        raise BillingProviderError("URL pública do CRM não configurada com HTTPS.")
    callback = f"{base}/checkout/status?{urlencode({'token': str(checkout.status_token)})}"
    label = {
        BillingCheckout.Kind.FIRST_COMPANY: "Gest Tech · primeira empresa",
        BillingCheckout.Kind.EXTRA_COMPANY: "Gest Tech · empresa adicional",
        BillingCheckout.Kind.EXTRA_NUMBER: "Gest Tech · número WhatsApp adicional",
    }[checkout.kind]
    due = timezone.localtime(timezone.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "billingTypes": ["CREDIT_CARD"], "chargeTypes": ["RECURRENT"],
        "minutesToExpire": 60, "externalReference": str(checkout.pk), "customer": customer_id,
        "callback": {"successUrl": callback, "cancelUrl": callback, "expiredUrl": callback},
        "items": [{"name": label, "description": "Assinatura mensal Gest Tech", "quantity": 1, "value": float(checkout.amount)}],
        "subscription": {"cycle": "MONTHLY", "nextDueDate": due},
    }


def provision_checkout(checkout):
    """Fail closed after an ambiguous provider response to prevent duplicate subscriptions."""
    with transaction.atomic():
        checkout = BillingCheckout.objects.select_for_update().get(pk=checkout.pk)
        client = AsaasClient()
        if checkout.asaas_environment != asaas_environment():
            raise BillingProviderError("Compra pertence a outro ambiente Asaas.")
        if checkout.asaas_checkout_id:
            return checkout
        if checkout.checkout_started_at:
            raise BillingProviderError("Compra em conciliação. Contate o suporte antes de tentar novamente.")
        checkout.checkout_started_at = timezone.now()
        checkout.save(update_fields=["checkout_started_at", "updated_at"])
    try:
        profile = BillingCustomer.objects.get(user=checkout.user)
        customer_id = ensure_customer(profile)
        result = client.request("POST", "/checkouts", json=checkout_payload(checkout, customer_id))
        checkout_id = result.get("id")
        try:
            uuid.UUID(str(checkout_id))
        except (ValueError, TypeError):
            raise BillingProviderError("Checkout Asaas não confirmado.")
    except BillingProviderError:
        BillingCheckout.objects.filter(pk=checkout.pk, status=BillingCheckout.Status.PENDING).update(status=BillingCheckout.Status.REVIEW)
        raise
    checkout.asaas_checkout_id = str(checkout_id)
    checkout.checkout_url = checkout_link(checkout)
    checkout.save(update_fields=["asaas_checkout_id", "checkout_url", "updated_at"])
    return checkout


def number_capacity(company):
    if company.billing_status == Company.BillingStatus.LEGACY:
        return None  # Preserve existing tenants until they opt into self-service billing.
    from .business_units import plan_for

    included = plan_for(company).included_whatsapp_numbers
    paid_slots = BillingCheckout.objects.filter(
        company=company, kind=BillingCheckout.Kind.EXTRA_NUMBER, status=BillingCheckout.Status.PAID,
        asaas_environment=asaas_environment(),
    ).filter(Q(overdue_since__isnull=True) | Q(overdue_since__gte=timezone.now() - PAYMENT_GRACE)).count()
    return included + paid_slots


def trial_available(company, *, now=None):
    now = now or timezone.now()
    return company.trial_expires_at is not None and company.trial_expires_at > now


def number_billing_available(number):
    """Keep the oldest connected numbers entitled when a paid extra slot lapses."""
    company = number.company
    if not number.is_active or not company_billing_available(company):
        return False
    capacity = number_capacity(company)
    if capacity is None:
        return True
    eligible_ids = WhatsAppNumber.objects.filter(company=company, is_active=True).order_by(
        "created_at", "pk",
    ).values_list("pk", flat=True)[:capacity]
    return number.pk in eligible_ids


def company_billing_available(company):
    if company.archived_at is not None:
        return False
    if company.billing_status == Company.BillingStatus.LEGACY:
        return True
    if trial_available(company):
        return True
    if company.billing_status != Company.BillingStatus.ACTIVE:
        return False
    return BillingCheckout.objects.filter(
        company=company,
        kind__in=[BillingCheckout.Kind.FIRST_COMPANY, BillingCheckout.Kind.EXTRA_COMPANY],
        status=BillingCheckout.Status.PAID,
        asaas_environment=asaas_environment(),
    ).filter(Q(overdue_since__isnull=True) | Q(overdue_since__gte=timezone.now() - PAYMENT_GRACE)).exists()


def accessible_companies():
    now = timezone.now()
    entitled_ids = BillingCheckout.objects.filter(
        kind__in=[BillingCheckout.Kind.FIRST_COMPANY, BillingCheckout.Kind.EXTRA_COMPANY],
        status=BillingCheckout.Status.PAID,
        asaas_environment=asaas_environment(),
    ).filter(Q(overdue_since__isnull=True) | Q(overdue_since__gte=timezone.now() - PAYMENT_GRACE)).values("company_id")
    return Company.objects.filter(archived_at__isnull=True).filter(Q(billing_status=Company.BillingStatus.LEGACY) | Q(
        trial_expires_at__gt=now,
    ) | Q(
        billing_status=Company.BillingStatus.ACTIVE, pk__in=entitled_ids,
    ))


def assert_number_capacity(company, *, existing_number=None):
    if not company_billing_available(company):
        raise ValidationError({"detail": "A assinatura desta empresa não está ativa."})
    if existing_number is not None and existing_number.is_active:
        return
    capacity = number_capacity(company)
    if capacity is None:
        return
    used = WhatsAppNumber.objects.filter(company=company, is_active=True).count()
    if used >= capacity:
        raise ValidationError({"detail": "O limite de números desta empresa foi atingido. Contrate uma vaga adicional."})


def reconcile_checkout(checkout_id):
    """One-off provider reconciliation for checkout-to-subscription and renewal state."""
    checkout = BillingCheckout.objects.select_related("user__billing_customer").get(pk=checkout_id)
    if (checkout.asaas_environment != asaas_environment()
            or checkout.status != BillingCheckout.Status.PAID or not checkout.asaas_checkout_id):
        return False
    client = AsaasClient()
    initial = client.request("GET", "/payments", params={"checkoutSession": checkout.asaas_checkout_id, "limit": 100})
    matching = [row for row in initial.get("data", []) if row.get("subscription")]
    matching = [row for row in matching if row.get("customer") == checkout.user.billing_customer.asaas_customer_id
                and Decimal(str(row.get("value", 0))) == checkout.amount]
    subscription_ids = {row["subscription"] for row in matching}
    if len(subscription_ids) != 1:
        raise BillingProviderError("Assinatura Asaas ainda não conciliada.")
    subscription_id = subscription_ids.pop()
    if checkout.asaas_subscription_id and checkout.asaas_subscription_id != subscription_id:
        raise BillingProviderError("Assinatura Asaas divergente.")
    subscription = client.request("GET", f"/subscriptions/{subscription_id}")
    if subscription.get("customer") != checkout.user.billing_customer.asaas_customer_id:
        raise BillingProviderError("Cliente da assinatura Asaas divergente.")
    payments = client.request("GET", "/payments", params={"subscription": subscription_id, "limit": 100})
    rows = payments.get("data", [])
    if payments.get("hasMore") or not isinstance(rows, list) or not rows:
        raise BillingProviderError("Cobranças Asaas ainda não conciliadas.")
    rows = [row for row in rows if row.get("customer") == checkout.user.billing_customer.asaas_customer_id
            and Decimal(str(row.get("value", 0))) == checkout.amount]
    if not rows:
        raise BillingProviderError("Cobranças Asaas divergentes.")
    rows.sort(key=lambda row: (str(row.get("dueDate") or ""), str(row.get("id") or "")))
    latest = rows[-1]
    try:
        due_date = date.fromisoformat(str(latest["dueDate"])[:10])
    except (KeyError, ValueError):
        raise BillingProviderError("Vencimento Asaas inválido.") from None
    status = latest.get("status")
    with transaction.atomic():
        checkout = BillingCheckout.objects.select_for_update().get(pk=checkout_id)
        if checkout.status != BillingCheckout.Status.PAID:
            return False
        checkout.asaas_subscription_id = subscription_id
        if checkout.last_due_date is None or due_date >= checkout.last_due_date:
            checkout.last_due_date = due_date
            if status in {"CONFIRMED", "RECEIVED"}:
                checkout.last_payment_id = str(latest.get("id") or "")[:80]
                checkout.overdue_since = None
            elif status == "OVERDUE" or status == "PENDING" and due_date < timezone.localdate():
                checkout.overdue_since = timezone.make_aware(datetime.combine(due_date, time.min))
            elif status in {"REFUNDED", "CHARGEBACK_REQUESTED"}:
                checkout.status = BillingCheckout.Status.REVOKED
                if checkout.kind != BillingCheckout.Kind.EXTRA_NUMBER:
                    Company.objects.filter(pk=checkout.company_id).update(billing_status=Company.BillingStatus.SUSPENDED)
        if subscription.get("status") != "ACTIVE" or subscription.get("deleted") is True:
            checkout.status = BillingCheckout.Status.REVOKED
            if checkout.kind != BillingCheckout.Kind.EXTRA_NUMBER:
                Company.objects.filter(pk=checkout.company_id).update(billing_status=Company.BillingStatus.SUSPENDED)
        checkout.save(update_fields=["asaas_subscription_id", "last_due_date", "last_payment_id", "overdue_since", "status", "updated_at"])
    return True


def process_asaas_event(payload):
    event_id = str(payload.get("id", ""))[:120]
    event_type = str(payload.get("event", ""))[:80]
    if not event_id or not event_type:
        raise ValidationError({"detail": "Evento inválido."})
    resource = payload.get("checkout") if isinstance(payload.get("checkout"), dict) else {}
    payment = payload.get("payment") if isinstance(payload.get("payment"), dict) else {}
    subscription = payload.get("subscription") if isinstance(payload.get("subscription"), dict) else {}
    checkout_id = str(resource.get("id") or payment.get("checkoutSession") or "")
    subscription_id = str(payment.get("subscription") or subscription.get("id") or "")
    reference = str(resource.get("externalReference") or payment.get("externalReference") or subscription.get("externalReference") or "")
    with transaction.atomic():
        checkout = BillingCheckout.objects.select_for_update().filter(asaas_checkout_id=checkout_id).first() if checkout_id else None
        if checkout is None and subscription_id:
            checkout = BillingCheckout.objects.select_for_update().filter(asaas_subscription_id=subscription_id).first()
        if checkout is None and reference:
            try:
                checkout = BillingCheckout.objects.select_for_update().filter(pk=uuid.UUID(reference)).first()
            except ValueError:
                pass
        if checkout and checkout.asaas_environment != asaas_environment():
            raise ValidationError({"detail": "Ambiente Asaas divergente."})
        if checkout and checkout_id and checkout.asaas_checkout_id and checkout_id != checkout.asaas_checkout_id:
            raise ValidationError({"detail": "Identificador de checkout divergente."})
        provider_customer = resource.get("customer") or payment.get("customer") or subscription.get("customer")
        if checkout and provider_customer and provider_customer != checkout.user.billing_customer.asaas_customer_id:
            raise ValidationError({"detail": "Cliente de cobrança divergente."})
        event, created = BillingEvent.objects.get_or_create(
            event_id=event_id, defaults={"event_type": event_type, "checkout": checkout},
        )
        if not created:
            return
        if checkout is None:
            return
        if checkout_id and not checkout.asaas_checkout_id:
            checkout.asaas_checkout_id = checkout_id
        if subscription_id and not checkout.asaas_subscription_id:
            checkout.asaas_subscription_id = subscription_id
        if event_type == "CHECKOUT_PAID" and resource.get("status") == "PAID":
            if not checkout.asaas_checkout_id or checkout_id != checkout.asaas_checkout_id:
                raise ValidationError({"detail": "Checkout Asaas não confirmado."})
            if checkout.status != BillingCheckout.Status.PAID:
                checkout.status = BillingCheckout.Status.PAID
                checkout.paid_at = timezone.now()
                if checkout.kind in {BillingCheckout.Kind.FIRST_COMPANY, BillingCheckout.Kind.EXTRA_COMPANY}:
                    Company.objects.filter(pk=checkout.company_id).update(billing_status=Company.BillingStatus.ACTIVE)
                if checkout.kind == BillingCheckout.Kind.FIRST_COMPANY:
                    user = checkout.user
                    if user.billing_customer.email_verified_at:
                        user.is_active = True
                        user.save(update_fields=["is_active"])
                from .tasks import reconcile_asaas_checkout
                transaction.on_commit(lambda checkout_pk=str(checkout.pk): reconcile_asaas_checkout.delay(checkout_pk))
        elif event_type in {"CHECKOUT_CANCELED", "CHECKOUT_EXPIRED"} and checkout.status == BillingCheckout.Status.PENDING:
            checkout.status = BillingCheckout.Status.CANCELLED if event_type == "CHECKOUT_CANCELED" else BillingCheckout.Status.EXPIRED
        if payment and event_type.startswith("PAYMENT_"):
            try:
                due_date = date.fromisoformat(str(payment.get("dueDate", ""))[:10])
            except ValueError:
                due_date = None
            if due_date and (checkout.last_due_date is None or due_date >= checkout.last_due_date):
                checkout.last_due_date = due_date
                if (event_type == "PAYMENT_OVERDUE" and checkout.status == BillingCheckout.Status.PAID
                        and str(payment.get("id", "")) != checkout.last_payment_id):
                    checkout.overdue_since = timezone.make_aware(datetime.combine(due_date, time.min))
                elif event_type in {"PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"}:
                    checkout.last_payment_id = str(payment.get("id", ""))[:80]
                    checkout.overdue_since = None
            if event_type in {"PAYMENT_REFUNDED", "PAYMENT_CHARGEBACK_REQUESTED"} and checkout.status == BillingCheckout.Status.PAID:
                payment_id = str(payment.get("id", ""))
                if payment_id and (payment_id == checkout.last_payment_id or not checkout.last_payment_id):
                    checkout.status = BillingCheckout.Status.REVOKED
                    if checkout.kind != BillingCheckout.Kind.EXTRA_NUMBER:
                        Company.objects.filter(pk=checkout.company_id).update(billing_status=Company.BillingStatus.SUSPENDED)
        if event_type in {"SUBSCRIPTION_INACTIVATED", "SUBSCRIPTION_DELETED"} and checkout.status == BillingCheckout.Status.PAID:
            checkout.status = BillingCheckout.Status.REVOKED
            if checkout.kind != BillingCheckout.Kind.EXTRA_NUMBER:
                Company.objects.filter(pk=checkout.company_id).update(billing_status=Company.BillingStatus.SUSPENDED)
        checkout.save(update_fields=[
            "asaas_checkout_id", "asaas_subscription_id", "status", "paid_at",
            "last_payment_id", "last_due_date", "overdue_since", "updated_at",
        ])
        event.checkout = checkout
        event.save(update_fields=["checkout"])
