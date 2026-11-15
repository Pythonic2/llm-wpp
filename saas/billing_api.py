"""Self-service Gest Tech billing, separate from tenant commercial sales."""

import hashlib
import hmac
import json
import re
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .billing import (
    AsaasClient, BillingProviderError, EXTRA_COMPANY_PRICE, EXTRA_NUMBER_PRICE, FIRST_COMPANY_PRICE,
    accessible_companies, company_billing_available, number_capacity, process_asaas_event, provision_checkout,
)
from .billing_email import confirm_email, queue_verification_email
from .models import BillingCheckout, BillingCustomer, Company, Membership, WhatsAppNumber

User = get_user_model()


def _digits(value):
    return re.sub(r"\D", "", str(value))


def _valid_cpf(value):
    if len(value) != 11 or len(set(value)) == 1:
        return False
    for length in (9, 10):
        total = sum(int(value[i]) * (length + 1 - i) for i in range(length))
        check = (total * 10) % 11
        if int(value[length]) != (0 if check == 10 else check):
            return False
    return True


def _valid_cnpj(value):
    if len(value) != 14 or len(set(value)) == 1:
        return False
    for length, weights in ((12, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
                            (13, (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2))):
        digit = (sum(int(a) * b for a, b in zip(value[:length], weights)) % 11)
        if int(value[length]) != (0 if digit < 2 else 11 - digit):
            return False
    return True


class BillingProfileInput(serializers.Serializer):
    full_name = serializers.CharField(max_length=160)
    cpf_cnpj = serializers.CharField(max_length=18)
    mobile_phone = serializers.CharField(max_length=20)
    postal_code = serializers.CharField(max_length=10)
    address = serializers.CharField(max_length=200)
    address_number = serializers.CharField(max_length=20)
    complement = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    province = serializers.CharField(max_length=100)
    city_name = serializers.CharField(max_length=120)
    state = serializers.CharField(max_length=2)

    def validate_cpf_cnpj(self, value):
        value = _digits(value)
        if not (_valid_cpf(value) or _valid_cnpj(value)):
            raise serializers.ValidationError("Informe CPF ou CNPJ válido.")
        return value

    def validate_mobile_phone(self, value):
        value = _digits(value)
        if value.startswith("55") and len(value) in (12, 13):
            value = value[2:]
        if len(value) not in (10, 11):
            raise serializers.ValidationError("Informe celular com DDD.")
        return value

    def validate_postal_code(self, value):
        value = _digits(value)
        if len(value) != 8:
            raise serializers.ValidationError("Informe CEP válido.")
        return value

    def validate_state(self, value):
        value = value.strip().upper()
        if value not in {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"}:
            raise serializers.ValidationError("Informe a sigla do estado.")
        return value


class SignupInput(BillingProfileInput):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(min_length=8, max_length=128, write_only=True)
    company_name = serializers.CharField(max_length=160)

    def validate_email(self, value):
        return value.strip().lower()

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return value


class ExtraCompanyInput(serializers.Serializer):
    company_name = serializers.CharField(max_length=160)
    billing_profile = BillingProfileInput(required=False)


class ExtraNumberInput(serializers.Serializer):
    billing_profile = BillingProfileInput(required=False)


def _new_company(name):
    from django.utils.text import slugify
    label = name.strip()
    slug = (slugify(label)[:35].rstrip("-") or "empresa") + "-" + uuid.uuid4().hex[:12]
    return Company.objects.create(name=label, slug=slug, billing_status=Company.BillingStatus.PENDING)


def _response(checkout):
    return {
        "id": str(checkout.pk), "status_token": str(checkout.status_token),
        "status": checkout.status, "kind": checkout.kind, "amount": str(checkout.amount),
        "checkout_url": checkout.checkout_url if checkout.status == BillingCheckout.Status.PENDING else "",
    }


def _provision_response(checkout):
    try:
        checkout = provision_checkout(checkout)
    except BillingProviderError as exc:
        checkout.refresh_from_db()
        return Response({**_response(checkout), "detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response(_response(checkout), status=status.HTTP_201_CREATED)


class PublicSignupView(APIView):
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "public_signup"

    @method_decorator(csrf_protect)
    def post(self, request):
        required = (
            settings.ASAAS_API_KEY, settings.ASAAS_WEBHOOK_AUTH_TOKEN,
            settings.GESTTECH_PUBLIC_URL, settings.GESTTECH_FROM_EMAIL,
        )
        smtp_required = (settings.EMAIL_HOST, settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD)
        if not all(required) or (not settings.DEBUG and not all(smtp_required)):
            return Response({"detail": "Cadastro temporariamente indisponível. Integração de pagamento ou e-mail não configurada."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            AsaasClient()
        except BillingProviderError:
            return Response({"detail": "Cadastro temporariamente indisponível. Integração de pagamento não configurada."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        serializer = SignupInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        email = data["email"]
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            existing_user = User.objects.filter(username__iexact=email, email__iexact=email).first()
            if existing_user and not existing_user.is_active and existing_user.check_password(data["password"]):
                existing_checkout = BillingCheckout.objects.filter(
                    user=existing_user, kind=BillingCheckout.Kind.FIRST_COMPANY,
                ).order_by("-created_at").first()
                if existing_checkout:
                    queue_verification_email(existing_user.billing_customer.pk)
                    return Response(_response(existing_checkout), status=status.HTTP_200_OK)
            raise ValidationError({"email": "Este e-mail já está cadastrado. Entre na sua conta ou contate o suporte."})
        profile_fields = {field: data[field] for field in BillingProfileInput().fields}
        try:
            with transaction.atomic():
                user = User.objects.create_user(username=email, email=email, password=data["password"], is_active=False)
                user.first_name = data["full_name"].split()[0]
                user.save(update_fields=["first_name"])
                company = _new_company(data["company_name"])
                Membership.objects.create(user=user, company=company, role=Membership.Role.OWNER)
                profile = BillingCustomer.objects.create(user=user, **profile_fields)
                checkout = BillingCheckout.objects.create(
                    user=user, company=company, kind=BillingCheckout.Kind.FIRST_COMPANY,
                    amount=FIRST_COMPANY_PRICE,
                )
                queue_verification_email(profile.pk)
        except IntegrityError as exc:
            raise ValidationError({"email": "Este e-mail já está cadastrado."}) from exc
        return _provision_response(checkout)


class PublicCheckoutStatusView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request, token):
        checkout = BillingCheckout.objects.filter(status_token=token).first()
        if checkout is None:
            return Response({"detail": "Compra não encontrada."}, status=status.HTTP_404_NOT_FOUND)
        response = Response({
            "status": checkout.status, "kind": checkout.kind, "amount": str(checkout.amount),
            "checkout_url": checkout.checkout_url if checkout.status == BillingCheckout.Status.PENDING else "",
            "email_verified": bool(checkout.user.billing_customer.email_verified_at),
            "can_login": checkout.kind == BillingCheckout.Kind.FIRST_COMPANY and checkout.status == BillingCheckout.Status.PAID and checkout.user.is_active,
        })
        response["Cache-Control"] = "no-store"
        return response


class PublicVerifyEmailView(APIView):
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "public_signup"

    @method_decorator(csrf_protect)
    def post(self, request):
        token = request.data.get("token")
        if not isinstance(token, str) or len(token) > 1000 or not confirm_email(token):
            raise ValidationError({"detail": "Link inválido ou expirado. Solicite um novo e-mail."})
        return Response({"verified": True})


class PublicResendEmailView(APIView):
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "public_signup"

    @method_decorator(csrf_protect)
    def post(self, request):
        email = str(request.data.get("email", "")).strip().lower()[:254]
        profile = BillingCustomer.objects.filter(user__email__iexact=email, email_verified_at__isnull=True).first()
        if profile:
            queue_verification_email(profile.pk)
        return Response({"detail": "Se houver um cadastro pendente, enviaremos um novo link."})


def _owner(request):
    membership = Membership.objects.filter(
        user=request.user, role=Membership.Role.OWNER,
        company__in=accessible_companies(),
    ).first()
    if not membership and not request.user.is_superuser:
        raise PermissionDenied("Somente o proprietário pode contratar recursos.")


def _profile_for_user(user, values):
    profile = BillingCustomer.objects.filter(user=user).first()
    if profile:
        return profile
    if not values:
        raise ValidationError({"billing_profile": "Informe os dados de cobrança para a primeira compra."})
    return BillingCustomer.objects.create(user=user, **values)


def _request_key(request):
    value = request.headers.get("Idempotency-Key", "").strip()
    if not value or len(value) > 128:
        raise ValidationError({"Idempotency-Key": "Informe uma chave única de até 128 caracteres."})
    return value


def _existing_request(user, request_key, request_hash):
    checkout = BillingCheckout.objects.filter(user=user, request_key=request_key).first()
    if checkout and checkout.request_hash != request_hash:
        raise ValidationError({"Idempotency-Key": "Chave já usada com outros dados."})
    return checkout


class ExtraCompanyCheckoutView(APIView):
    permission_classes = (IsAuthenticated,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "billing_purchase"

    def post(self, request):
        _owner(request)
        serializer = ExtraCompanyInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        request_key = _request_key(request)
        request_hash = hashlib.sha256(json.dumps(request.data, sort_keys=True, default=str).encode()).hexdigest()
        existing = _existing_request(request.user, request_key, request_hash)
        if existing:
            return Response(_response(existing))
        try:
            with transaction.atomic():
                _profile_for_user(request.user, values.get("billing_profile"))
                company = _new_company(values["company_name"])
                Membership.objects.create(user=request.user, company=company, role=Membership.Role.OWNER)
                checkout = BillingCheckout.objects.create(
                    user=request.user, company=company, kind=BillingCheckout.Kind.EXTRA_COMPANY,
                    amount=EXTRA_COMPANY_PRICE, request_key=request_key, request_hash=request_hash,
                )
        except IntegrityError:
            existing = _existing_request(request.user, request_key, request_hash)
            if existing:
                return Response(_response(existing))
            raise ValidationError({"detail": "Compra já em andamento. Consulte sua lista de empresas."})
        return _provision_response(checkout)


class BillingProfileStatusView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        return Response({"configured": BillingCustomer.objects.filter(user=request.user).exists()})


class ExtraNumberCheckoutView(APIView):
    permission_classes = (IsAuthenticated,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "billing_purchase"

    def post(self, request, company_id):
        membership = Membership.objects.filter(user=request.user, company_id=company_id, role=Membership.Role.OWNER).select_related("company").first()
        if membership is None and not request.user.is_superuser:
            raise PermissionDenied("Somente o proprietário desta empresa pode contratar números.")
        company = membership.company if membership else Company.objects.get(pk=company_id)
        if not company_billing_available(company):
            raise ValidationError({"detail": "Ative a assinatura da empresa primeiro."})
        serializer = ExtraNumberInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        request_key = _request_key(request)
        request_hash = hashlib.sha256(json.dumps(request.data, sort_keys=True, default=str).encode()).hexdigest()
        existing = _existing_request(request.user, request_key, request_hash)
        if existing:
            return Response(_response(existing))
        try:
            with transaction.atomic():
                _profile_for_user(request.user, serializer.validated_data.get("billing_profile"))
                checkout = BillingCheckout.objects.create(
                    user=request.user, company=company, kind=BillingCheckout.Kind.EXTRA_NUMBER,
                    amount=EXTRA_NUMBER_PRICE, request_key=request_key, request_hash=request_hash,
                )
        except IntegrityError:
            existing = _existing_request(request.user, request_key, request_hash)
            if existing:
                return Response(_response(existing))
            raise ValidationError({"detail": "Compra já em andamento. Atualize a página."})
        return _provision_response(checkout)


class BillingQuotaView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id):
        membership = Membership.objects.filter(user=request.user, company_id=company_id).select_related("company").first()
        if membership is None and not request.user.is_superuser:
            raise PermissionDenied("Empresa não encontrada.")
        company = membership.company if membership else Company.objects.get(pk=company_id)
        capacity = number_capacity(company)
        used = WhatsAppNumber.objects.filter(company=company, is_active=True).count()
        return Response({
            "company_status": company.billing_status, "included_numbers": 2,
            "access_active": company_billing_available(company),
            "paid_extra_numbers": max(0, capacity - 2) if capacity is not None else 0,
            "number_capacity": capacity, "active_numbers": used,
            "first_company_price": str(FIRST_COMPANY_PRICE), "extra_company_price": str(EXTRA_COMPANY_PRICE),
            "extra_number_price": str(EXTRA_NUMBER_PRICE),
            "can_purchase": bool(request.user.is_superuser or membership and membership.role == Membership.Role.OWNER),
        })


@csrf_exempt
def asaas_webhook(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Método inválido."}, status=405)
    expected = settings.ASAAS_WEBHOOK_AUTH_TOKEN
    supplied = request.headers.get("asaas-access-token", "")
    if not expected or not hmac.compare_digest(supplied, expected):
        return JsonResponse({"detail": "Não autorizado."}, status=403)
    if len(request.body) > 128_000:
        return JsonResponse({"detail": "Evento muito grande."}, status=413)
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError("payload")
        process_asaas_event(payload)
    except (ValueError, ValidationError):
        return JsonResponse({"detail": "Evento inválido."}, status=400)
    return JsonResponse({"ok": True})
