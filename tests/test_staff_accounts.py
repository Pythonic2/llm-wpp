from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from rest_framework.test import APIClient

from saas.models import Company, Membership, StaffPasswordState

pytestmark = pytest.mark.django_db
URL = "/api/v1/team/staff/"


def _admin_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_admin_creates_staff_for_selected_companies_without_email(tenant_data):
    second = Company.objects.create(name="Empresa C", slug="empresa-c")
    Membership.objects.create(company=second, user=tenant_data["user"], role="owner")
    with patch("saas.staff_api.send_mail") as send:
        response = _admin_client(tenant_data["user"]).post(URL, {
            "username": "funcionario", "password": "1234", "role": "agent",
            "company_ids": [str(tenant_data["company"].pk), str(second.pk)],
        }, format="json")
    assert response.status_code == 201, response.data
    user = get_user_model().objects.get(username="funcionario")
    assert user.is_active and user.check_password("1234") and user.email == ""
    assert StaffPasswordState.objects.get(user=user).must_change_password
    assert set(Membership.objects.filter(user=user).values_list("company_id", flat=True)) == {tenant_data["company"].pk, second.pk}
    assert all(role == "agent" for role in Membership.objects.filter(user=user).values_list("role", flat=True))
    assert "password" not in response.data and "password" not in response.data["user"]
    send.assert_not_called()


def test_staff_creation_rejects_cross_tenant_and_agent_without_partial_account(tenant_data):
    other = _admin_client(tenant_data["other"]).post(URL, {
        "username": "cross", "password": "1234",
        "company_ids": [str(tenant_data["company"].pk)],
    }, format="json")
    assert other.status_code == 404
    partial = _admin_client(tenant_data["user"]).post(URL, {
        "username": "partial", "password": "1234",
        "company_ids": [str(tenant_data["company"].pk), str(tenant_data["company_b"].pk)],
    }, format="json")
    assert partial.status_code == 404
    assert not get_user_model().objects.filter(username__in=["cross", "partial"]).exists()
    agent = get_user_model().objects.create_user("agent-staff", password="test")
    Membership.objects.create(company=tenant_data["company"], user=agent, role="agent")
    denied = _admin_client(agent).post(URL, {
        "username": "agent-created", "password": "1234", "company_ids": [str(tenant_data["company"].pk)],
    }, format="json")
    assert denied.status_code == 403


def test_staff_creation_sends_notice_only_with_email(settings, tenant_data, django_capture_on_commit_callbacks):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.EMAIL_HOST_USER = "smtp@example.com"
    settings.EMAIL_HOST_PASSWORD = "configured"
    settings.GESTTECH_FROM_EMAIL = "smtp@example.com"
    with django_capture_on_commit_callbacks(execute=True):
        response = _admin_client(tenant_data["user"]).post(URL, {
            "username": "staff-email", "password": "1234", "email": "staff@example.com",
            "company_ids": [str(tenant_data["company"].pk)],
        }, format="json")
    assert response.status_code == 201, response.data
    assert len(mail.outbox) == 1
    assert "staff-email" in mail.outbox[0].body and "1234" in mail.outbox[0].body
    assert tenant_data["company"].name in mail.outbox[0].body
    assert _admin_client(tenant_data["user"]).post(URL, {
        "username": "STAFF-EMAIL", "password": "1234", "company_ids": [str(tenant_data["company"].pk)],
    }, format="json").status_code == 400


def test_staff_must_change_password_before_accessing_data(tenant_data):
    created = _admin_client(tenant_data["user"]).post(URL, {
        "username": "first-access", "password": "1234", "company_ids": [str(tenant_data["company"].pk)],
    }, format="json")
    assert created.status_code == 201
    client = APIClient()
    login = client.post("/api/v1/auth/login/", {"username": "first-access", "password": "1234"}, format="json")
    assert login.status_code == 200, login.data
    assert login.data["user"]["must_change_password"] is True
    assert client.get("/api/v1/auth/me/").status_code == 200
    blocked = client.get("/api/v1/companies/")
    assert blocked.status_code == 403 and blocked.json()["code"] == "password_change_required"
    assert client.post("/api/v1/auth/initial-password/", {"current_password": "wrong", "new_password": "NewStrongPassword-2026"}, format="json").status_code == 400
    assert client.post("/api/v1/auth/initial-password/", {"current_password": "1234", "new_password": "1234"}, format="json").status_code == 400
    changed = client.post("/api/v1/auth/initial-password/", {"current_password": "1234", "new_password": "NewStrongPassword-2026"}, format="json")
    assert changed.status_code == 200, changed.data
    assert changed.data["user"]["must_change_password"] is False
    assert client.get("/api/v1/companies/").status_code == 200
    client.post("/api/v1/auth/logout/")
    assert client.post("/api/v1/auth/login/", {"username": "first-access", "password": "1234"}, format="json").status_code == 400
