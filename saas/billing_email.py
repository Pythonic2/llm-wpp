"""Proof of mailbox ownership for paid public signups."""

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import BillingCheckout, BillingCustomer

logger = logging.getLogger(__name__)
SALT = "gesttech-billing-email-v1"
MAX_AGE_SECONDS = 48 * 60 * 60


def send_verification_email(profile_id):
    profile = BillingCustomer.objects.select_related("user").get(pk=profile_id)
    if profile.email_verified_at:
        return
    token = signing.dumps({"user_id": profile.user_id, "email": profile.user.email}, salt=SALT)
    url = f"{settings.GESTTECH_PUBLIC_URL.rstrip('/')}/verify-email?{urlencode({'token': token})}"
    send_mail(
        "Confirme seu e-mail no Gest Tech",
        f"Para confirmar seu e-mail e liberar sua conta após o pagamento, abra este link: {url}\n\nEste link expira em 48 horas.",
        settings.GESTTECH_FROM_EMAIL,
        [profile.user.email],
        fail_silently=False,
    )


def queue_verification_email(profile_id):
    def deliver():
        try:
            send_verification_email(profile_id)
        except Exception:
            logger.exception("Falha ao enviar verificação de e-mail; billing_profile_id=%s", profile_id)

    transaction.on_commit(deliver)


def confirm_email(token):
    try:
        data = signing.loads(token, salt=SALT, max_age=MAX_AGE_SECONDS)
        user_id = data["user_id"]
        email = data["email"]
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return False
    with transaction.atomic():
        profile = BillingCustomer.objects.select_for_update().select_related("user").filter(user_id=user_id).first()
        if profile is None or profile.user.email != email:
            return False
        if profile.email_verified_at is None:
            profile.email_verified_at = timezone.now()
            profile.save(update_fields=["email_verified_at", "updated_at"])
        if BillingCheckout.objects.filter(
            user_id=user_id, kind=BillingCheckout.Kind.FIRST_COMPANY,
            status=BillingCheckout.Status.PAID,
        ).exists():
            user = get_user_model().objects.select_for_update().get(pk=user_id)
            if not user.is_active:
                user.is_active = True
                user.save(update_fields=["is_active"])
    return True
