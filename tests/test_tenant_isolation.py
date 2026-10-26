import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from saas.admin import ContactAdmin
from saas.models import Contact, Conversation, Message

pytestmark = pytest.mark.django_db

def test_admin_queryset_only_contains_user_companies(tenant_data):
    request = RequestFactory().get("/admin/saas/contact/")
    request.user = tenant_data["user"]
    assert list(ContactAdmin(Contact, AdminSite()).get_queryset(request)) == [tenant_data["contact"]]

def test_conversation_rejects_cross_company_relations(tenant_data):
    obj = Conversation(company=tenant_data["company"], whatsapp_number=tenant_data["number_b"], contact=tenant_data["contact"])
    with pytest.raises(ValidationError, match="Número pertence"): obj.full_clean()

def test_message_rejects_cross_company_conversation(tenant_data):
    obj = Message(company=tenant_data["company_b"], conversation=tenant_data["conversation"], external_id="wamid.cross", direction="in")
    with pytest.raises(ValidationError, match="Conversa pertence"): obj.full_clean()
