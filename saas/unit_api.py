"""Company-scoped business unit operations."""

from django.db import IntegrityError, transaction
from django.utils.text import slugify
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .api import _company_for_request
from .business_units import create_unit, resolve_unit, set_unit_active, unit_capacity
from .models import BusinessUnit, BusinessUnitEvent, KnowledgeDocument, KnowledgeDocumentShare, WhatsAppNumber


class BusinessUnitSerializer(serializers.ModelSerializer):
    whatsapp_numbers = serializers.SerializerMethodField()

    class Meta:
        model = BusinessUnit
        fields = (
            "id", "name", "slug", "trade_name", "activity_description", "is_default",
            "is_active", "ai_instructions", "whatsapp_numbers", "created_at", "updated_at",
        )
        read_only_fields = ("id", "is_default", "whatsapp_numbers", "created_at", "updated_at")

    def get_whatsapp_numbers(self, obj):
        return [{"id": number.pk, "display_phone_number": number.display_phone_number}
                for number in obj.whatsapp_numbers.all()]


class UnitListView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id):
        company, _ = _company_for_request(request, company_id)
        units = BusinessUnit.objects.filter(company=company).prefetch_related("whatsapp_numbers").order_by("created_at", "pk")
        return Response({"results": BusinessUnitSerializer(units, many=True).data,
                         "used": units.count(), "limit": unit_capacity(company),
                         "over_limit": units.count() > unit_capacity(company)})

    def post(self, request, company_id):
        company, _ = _company_for_request(request, company_id, "admin")
        name = str(request.data.get("name", "")).strip()[:160]
        if not name:
            raise ValidationError({"name": "Informe o nome da unidade."})
        slug = slugify(name)[:70].strip("-")
        if not slug:
            raise ValidationError({"name": "Informe um nome válido."})
        try:
            unit = create_unit(
                company, request.user, name=name, slug=slug,
                trade_name=str(request.data.get("trade_name", "")).strip()[:160],
                activity_description=str(request.data.get("activity_description", "")).strip()[:1000],
            )
        except IntegrityError as exc:
            raise ValidationError({"name": "Já existe uma unidade com este nome."}) from exc
        return Response(BusinessUnitSerializer(unit).data, status=status.HTTP_201_CREATED)


class UnitDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def patch(self, request, company_id, unit_id):
        company, _ = _company_for_request(request, company_id, "admin")
        unit = BusinessUnit.objects.filter(company=company, pk=unit_id).first()
        if unit is None:
            raise NotFound("Unidade não encontrada.")
        allowed = {"name", "trade_name", "activity_description", "ai_instructions", "is_active"}
        if any(key not in allowed for key in request.data):
            raise ValidationError({"detail": "Campo não permitido."})
        with transaction.atomic():
            unit = BusinessUnit.objects.select_for_update().get(pk=unit.pk)
            if "is_active" in request.data:
                active = request.data["is_active"]
                if not isinstance(active, bool):
                    raise ValidationError({"is_active": "Informe true ou false."})
                if active != unit.is_active:
                    set_unit_active(unit, request.user, active)
            fields = []
            for key, limit in (("name", 160), ("trade_name", 160), ("activity_description", 1000), ("ai_instructions", 8000)):
                if key in request.data:
                    value = str(request.data[key]).strip()
                    if key == "name" and not value:
                        raise ValidationError({"name": "Informe o nome da unidade."})
                    if len(value) > limit:
                        raise ValidationError({key: "Texto muito longo."})
                    setattr(unit, key, value)
                    fields.append(key)
            if fields:
                unit.save(update_fields=fields + ["updated_at"])
                BusinessUnitEvent.objects.create(
                    company=company, business_unit=unit, actor=request.user,
                    kind="settings_changed", details={"fields": fields},
                )
        unit.refresh_from_db()
        return Response(BusinessUnitSerializer(unit).data)


class NumberUnitView(APIView):
    permission_classes = (IsAuthenticated,)

    def patch(self, request, company_id, number_id):
        company, _ = _company_for_request(request, company_id, "admin")
        if not request.data.get("business_unit_id"):
            raise ValidationError({"business_unit_id": "Informe a unidade de destino."})
        unit = resolve_unit(company, request.data.get("business_unit_id"))
        with transaction.atomic():
            number = WhatsAppNumber.objects.select_for_update().filter(company=company, pk=number_id).first()
            if number is None:
                raise NotFound("Número não encontrado.")
            previous = str(number.business_unit_id or "")
            number.business_unit = unit
            number.save(update_fields=["business_unit"])
            BusinessUnitEvent.objects.create(
                company=company, business_unit=unit, actor=request.user,
                kind="number_moved", details={"number_id": number.pk, "previous_unit_id": previous},
            )
        return Response({"number_id": number.pk, "business_unit_id": str(unit.pk)})


class DocumentShareView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request, company_id, document_id):
        company, _ = _company_for_request(request, company_id, "admin")
        if not request.data.get("business_unit_id"):
            raise ValidationError({"business_unit_id": "Informe a unidade de destino."})
        document = KnowledgeDocument.objects.filter(company=company, pk=document_id).first()
        if document is None:
            raise NotFound("Documento não encontrado.")
        unit = resolve_unit(company, request.data.get("business_unit_id"))
        if document.knowledge_base.business_unit_id == unit.pk:
            raise ValidationError({"detail": "O documento já pertence a esta unidade."})
        share, created = KnowledgeDocumentShare.objects.get_or_create(
            document=document, business_unit=unit, defaults={"granted_by": request.user},
        )
        if created:
            BusinessUnitEvent.objects.create(
                company=company, business_unit=unit, actor=request.user,
                kind="document_shared", details={"document_id": document.pk},
            )
        return Response({"document_id": document.pk, "business_unit_id": str(unit.pk)},
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def delete(self, request, company_id, document_id):
        company, _ = _company_for_request(request, company_id, "admin")
        if not request.data.get("business_unit_id"):
            raise ValidationError({"business_unit_id": "Informe a unidade de destino."})
        document = KnowledgeDocument.objects.filter(company=company, pk=document_id).first()
        if document is None:
            raise NotFound("Documento não encontrado.")
        unit = resolve_unit(company, request.data.get("business_unit_id"))
        removed, _ = KnowledgeDocumentShare.objects.filter(document=document, business_unit=unit).delete()
        if removed:
            BusinessUnitEvent.objects.create(
                company=company, business_unit=unit, actor=request.user,
                kind="document_unshared", details={"document_id": document.pk},
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
