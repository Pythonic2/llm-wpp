"""One-time password setup for new team members."""

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import transaction
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import Membership

logger = logging.getLogger(__name__)
SALT = "gesttech-team-invite-v1"
MAX_AGE_SECONDS = 48 * 60 * 60


def queue_team_invite(membership_id):
    def deliver():
        try:
            membership = Membership.objects.select_related("user", "company").get(pk=membership_id)
            if membership.user.is_active:
                return
            token = signing.dumps({
                "user_id": membership.user_id,
                "email": membership.user.email,
                "membership_id": membership.pk,
                "company_id": str(membership.company_id),
            }, salt=SALT)
            url = f"{settings.GESTTECH_PUBLIC_URL.rstrip('/')}/invite/accept?{urlencode({'token': token})}"
            send_mail(
                "Convite para a equipe no Gest Tech",
                f"Você foi convidado para a equipe de {membership.company.name}. "
                f"Defina sua senha para acessar o CRM: {url}\n\nEste link expira em 48 horas.",
                settings.GESTTECH_FROM_EMAIL,
                [membership.user.email],
                fail_silently=False,
            )
        except Exception:
            logger.exception("Falha ao enviar convite de equipe; membership_id=%s", membership_id)

    transaction.on_commit(deliver)


class AcceptTeamInviteInput(serializers.Serializer):
    token = serializers.CharField(max_length=2000)
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class AcceptTeamInviteView(APIView):
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "team_invite_accept"

    def post(self, request):
        data = AcceptTeamInviteInput(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            payload = signing.loads(data.validated_data["token"], salt=SALT, max_age=MAX_AGE_SECONDS)
            user_id = payload["user_id"]
            email = payload["email"]
            membership_id = payload["membership_id"]
            company_id = payload["company_id"]
        except (signing.BadSignature, KeyError, TypeError, ValueError):
            return Response({"detail": "Convite inválido ou expirado."}, status=400)
        User = get_user_model()
        with transaction.atomic():
            user = User.objects.select_for_update().filter(pk=user_id).first()
            membership = Membership.objects.filter(pk=membership_id, user_id=user_id, company_id=company_id).first()
            if user is None or membership is None or user.email != email or user.is_active:
                return Response({"detail": "Convite inválido ou já utilizado."}, status=400)
            try:
                validate_password(data.validated_data["password"], user)
            except DjangoValidationError as exc:
                return Response({"password": exc.messages}, status=400)
            user.set_password(data.validated_data["password"])
            user.is_active = True
            user.save(update_fields=["password", "is_active"])
        return Response({"ok": True})
