import re
from unittest.mock import patch
from urllib.parse import unquote

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core import signing
from rest_framework.test import APIClient

from saas.models import Membership
from saas.team_invites import MAX_AGE_SECONDS, SALT

pytestmark = pytest.mark.django_db


def _member_url(company):
    return f"/api/v1/companies/{company.pk}/members/"


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_new_employee_gets_email_and_activates_once(settings, tenant_data, django_capture_on_commit_callbacks):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.EMAIL_HOST_USER = "smtp@example.com"
    settings.EMAIL_HOST_PASSWORD = "configured"
    settings.GESTTECH_FROM_EMAIL = "smtp@example.com"
    settings.GESTTECH_PUBLIC_URL = "https://crm.example.com"
    with django_capture_on_commit_callbacks(execute=True):
        response = _client(tenant_data["user"]).post(
            _member_url(tenant_data["company"]), {"email": "staff@example.com", "role": "agent"}, format="json",
        )
    assert response.status_code == 201, response.data
    user = get_user_model().objects.get(email="staff@example.com")
    assert not user.is_active and not user.has_usable_password()
    assert Membership.objects.filter(company=tenant_data["company"], user=user, role="agent").exists()
    assert len(mail.outbox) == 1
    token = unquote(re.search(r"token=([^\s]+)", mail.outbox[0].body).group(1))
    accept_url = "/api/v1/public/team-invites/accept/"
    accepted = APIClient().post(accept_url, {"token": token, "password": "Secure-staff-password-2026"}, format="json")
    assert accepted.status_code == 200, accepted.data
    user.refresh_from_db()
    assert user.is_active and user.check_password("Secure-staff-password-2026")
    assert APIClient().post(accept_url, {"token": token, "password": "Other-password-2026"}, format="json").status_code == 400


def test_team_invite_is_tenant_scoped_and_revocable(settings, tenant_data, django_capture_on_commit_callbacks):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.EMAIL_HOST_USER = "smtp@example.com"
    settings.EMAIL_HOST_PASSWORD = "configured"
    settings.GESTTECH_FROM_EMAIL = "smtp@example.com"
    settings.GESTTECH_PUBLIC_URL = "https://crm.example.com"
    target = _member_url(tenant_data["company"])
    assert _client(tenant_data["other"]).post(target, {"email": "staff@example.com"}, format="json").status_code == 404
    with django_capture_on_commit_callbacks(execute=True):
        response = _client(tenant_data["user"]).post(target, {"email": "staff@example.com"}, format="json")
    token = unquote(re.search(r"token=([^\s]+)", mail.outbox[-1].body).group(1))
    Membership.objects.filter(pk=response.data["id"]).delete()
    assert APIClient().post("/api/v1/public/team-invites/accept/", {"token": token, "password": "Secure-staff-password-2026"}, format="json").status_code == 400


def test_existing_employee_is_added_without_invite(tenant_data):
    response = _client(tenant_data["user"]).post(
        _member_url(tenant_data["company"]), {"username": tenant_data["other"].username, "role": "agent"}, format="json",
    )
    assert response.status_code == 201
    assert Membership.objects.filter(company=tenant_data["company"], user=tenant_data["other"]).exists()


def test_unknown_username_explains_email_invitation(tenant_data):
    response = _client(tenant_data["user"]).post(
        _member_url(tenant_data["company"]), {"username": "teste", "role": "agent"}, format="json",
    )
    assert response.status_code == 400
    assert "e-mail" in str(response.data)


def test_agent_cannot_invite_or_change_role(settings, tenant_data):
    settings.EMAIL_HOST_USER = "smtp@example.com"
    settings.EMAIL_HOST_PASSWORD = "configured"
    settings.GESTTECH_PUBLIC_URL = "https://crm.example.com"
    agent = get_user_model().objects.create_user("agent-test", password="test")
    Membership.objects.create(company=tenant_data["company"], user=agent, role="agent")
    response = _client(agent).post(
        _member_url(tenant_data["company"]), {"email": "staff@example.com", "role": "admin"}, format="json",
    )
    assert response.status_code == 403
    assert not get_user_model().objects.filter(email="staff@example.com").exists()


def test_bad_token_and_missing_smtp_do_not_activate_or_create(settings, tenant_data):
    settings.EMAIL_HOST_USER = ""
    settings.EMAIL_HOST_PASSWORD = ""
    settings.GESTTECH_PUBLIC_URL = "https://crm.example.com"
    response = _client(tenant_data["user"]).post(
        _member_url(tenant_data["company"]), {"email": "staff@example.com", "role": "agent"}, format="json",
    )
    assert response.status_code == 400
    assert not get_user_model().objects.filter(email="staff@example.com").exists()
    invalid = APIClient().post(
        "/api/v1/public/team-invites/accept/", {"token": "tampered", "password": "Secure-staff-password-2026"}, format="json",
    )
    assert invalid.status_code == 400


def test_expired_invite_cannot_activate_employee(tenant_data):
    user = get_user_model().objects.create_user("pending-staff", email="staff@example.com", password=None, is_active=False)
    member = Membership.objects.create(company=tenant_data["company"], user=user, role="agent")
    with patch("django.core.signing.time.time", return_value=1_000):
        token = signing.dumps({
            "user_id": user.pk, "email": user.email,
            "membership_id": member.pk, "company_id": str(member.company_id),
        }, salt=SALT)
    with patch("django.core.signing.time.time", return_value=1_000 + MAX_AGE_SECONDS + 1):
        response = APIClient().post(
            "/api/v1/public/team-invites/accept/", {"token": token, "password": "Secure-staff-password-2026"}, format="json",
        )
    assert response.status_code == 400
    user.refresh_from_db()
    assert not user.is_active
