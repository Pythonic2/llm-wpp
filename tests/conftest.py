import pytest
from django.contrib.auth import get_user_model
from saas.models import Company, Contact, Conversation, Membership, WhatsAppNumber

@pytest.fixture
def tenant_data(db):
    user = get_user_model().objects.create_user("operator", password="test")
    other = get_user_model().objects.create_user("other", password="test")
    company = Company.objects.create(name="Empresa A", slug="empresa-a")
    company_b = Company.objects.create(name="Empresa B", slug="empresa-b")
    Membership.objects.create(company=company, user=user, role="admin")
    Membership.objects.create(company=company_b, user=other, role="admin")
    number = WhatsAppNumber.objects.create(company=company, phone_number_id="meta-test-id", display_phone_number="+15550001")
    number_b = WhatsAppNumber.objects.create(company=company_b, phone_number_id="meta-other-id", display_phone_number="+15550002")
    contact = Contact.objects.create(company=company, wa_id="5511999999999")
    contact_b = Contact.objects.create(company=company_b, wa_id="5511888888888")
    conversation = Conversation.objects.create(company=company, whatsapp_number=number, contact=contact)
    return locals()
