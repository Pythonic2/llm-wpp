"""Direct staff accounts with scoped company access and initial password change."""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .api import _company_for_request
from .api_serializers import UserSerializer
from .models import Membership, StaffPasswordState

logger = logging.getLogger(__name__)
User = get_user_model()


class StaffCreateInput(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(min_length=4, max_length=128, trim_whitespace=False, write_only=True)
    email = serializers.EmailField(max_length=254, allow_blank=True, required=False)
    role = serializers.ChoiceField(choices=(Membership.Role.ADMIN, Membership.Role.AGENT), default=Membership.Role.AGENT)
    company_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=25)

    def validate_username(self, value):
        try:
            UnicodeUsernameValidator()(value)
        except DjangoValidationError:
            raise serializers.ValidationError("Use apenas letras, números e @/./+/-/_ no usuário.")
        return value

    def validate_password(self, value):
        if not value.strip():
            raise serializers.ValidationError("Defina uma senha inicial.")
        return value

    def validate_company_ids(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Escolha cada empresa apenas uma vez.")
        return value


def _send_staff_notice(email, username, password, company_names):
    try:
        send_mail(
            "Acesso ao Gest Tech",
            "Você foi adicionado às empresas: " + ", ".join(company_names)
            + f".\n\nUsuário: {username}\nSenha inicial: {password}"
            + "\n\nTroque a senha no primeiro acesso.",
            settings.GESTTECH_FROM_EMAIL,
            [email],
            fail_silently=False,
        )
    except Exception as exc:
        logger.error("Falha ao notificar funcionário recém-criado por e-mail: %s", type(exc).__name__)


class StaffCreateView(APIView):
    permission_classes = (IsAuthenticated,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "team_staff_create"

    def post(self, request):
        data = StaffCreateInput(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        username = values["username"]
        email = values.get("email", "").strip().lower()
        password = values["password"]
        companies = [_company_for_request(request, company_id, "admin")[0] for company_id in values["company_ids"]]
        if email and (not settings.EMAIL_HOST_USER or not settings.EMAIL_HOST_PASSWORD or not settings.GESTTECH_FROM_EMAIL):
            raise ValidationError({"email": "Notificação por e-mail indisponível. Deixe o e-mail em branco ou configure o SMTP."})
        try:
            with transaction.atomic():
                if connection.vendor == "postgresql":
                    with connection.cursor() as cursor:
                        for identity in sorted({username.casefold(), email.casefold()} - {""}):
                            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [identity])
                if User.objects.filter(Q(username__iexact=username) | Q(email__iexact=username)).exists():
                    raise ValidationError({"username": "Este usuário já está em uso."})
                if email and User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exists():
                    raise ValidationError({"email": "Este e-mail já está em uso."})
                user = User.objects.create_user(username=username, email=email, password=password)
                StaffPasswordState.objects.create(user=user, must_change_password=True)
                Membership.objects.bulk_create([
                    Membership(company=company, user=user, role=values["role"]) for company in companies
                ])
                if email:
                    company_names = [company.name for company in companies]
                    transaction.on_commit(lambda: _send_staff_notice(email, username, password, company_names))
        except IntegrityError:
            raise ValidationError({"username": "Este usuário já está em uso."})
        return Response({"user": UserSerializer(user).data, "company_ids": [str(company.pk) for company in companies]}, status=status.HTTP_201_CREATED)


class InitialPasswordView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        current = request.data.get("current_password", "")
        new = request.data.get("new_password", "")
        if not isinstance(current, str) or not isinstance(new, str) or not current or not new:
            raise ValidationError({"detail": "Informe a senha inicial e a nova senha."})
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            state = StaffPasswordState.objects.select_for_update().filter(user=user, must_change_password=True).first()
            if state is None:
                raise ValidationError({"detail": "Esta conta não tem troca de senha pendente."})
            if not user.check_password(current):
                raise ValidationError({"current_password": "Senha inicial incorreta."})
            if current == new:
                raise ValidationError({"new_password": "Escolha uma senha diferente da inicial."})
            try:
                validate_password(new, user=user)
            except DjangoValidationError as exc:
                raise ValidationError({"new_password": exc.messages})
            user.set_password(new)
            user.save(update_fields=["password"])
            state.must_change_password = False
            state.save(update_fields=["must_change_password"])
        update_session_auth_hash(request, user)
        return Response({"user": UserSerializer(user).data})
