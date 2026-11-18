"""Unit ownership, quota and historical attribution rules."""

import uuid

from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import BusinessUnit, BusinessUnitEvent, Company, PlanVersion


def plan_for(company):
    if company.plan_id:
        return company.plan
    return PlanVersion.objects.get(code="basic", version=2)


def default_unit(company):
    return BusinessUnit.objects.get(company=company, is_default=True)


def unit_capacity(company):
    return plan_for(company).included_business_units


def resolve_unit(company, unit_id=None, *, require_explicit=False, active=True):
    if unit_id:
        try:
            unit_id = uuid.UUID(str(unit_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError({"business_unit_id": "Unidade inválida."}) from exc
        unit = BusinessUnit.objects.filter(company=company, pk=unit_id).first()
        if unit is None:
            raise ValidationError({"business_unit_id": "Unidade não encontrada nesta empresa."})
    else:
        if require_explicit and BusinessUnit.objects.filter(company=company, is_active=True).count() > 1:
            raise ValidationError({"business_unit_id": "Selecione a unidade de negócio."})
        unit = default_unit(company)
    if active and not unit.is_active:
        raise ValidationError({"business_unit_id": "A unidade está inativa."})
    return unit


def create_unit(company, actor, *, name, slug, trade_name="", activity_description=""):
    with transaction.atomic():
        company = Company.objects.select_for_update().get(pk=company.pk)
        if BusinessUnit.objects.filter(company=company).count() >= unit_capacity(company):
            raise ValidationError({"detail": "O limite de unidades do plano foi atingido."})
        unit = BusinessUnit.objects.create(
            company=company, name=name, slug=slug, trade_name=trade_name,
            activity_description=activity_description,
        )
        BusinessUnitEvent.objects.create(company=company, business_unit=unit, actor=actor, kind="created")
        return unit


def set_unit_active(unit, actor, active):
    if unit.is_default and not active:
        raise ValidationError({"detail": "A unidade padrão não pode ser desativada."})
    with transaction.atomic():
        unit = BusinessUnit.objects.select_for_update().get(pk=unit.pk)
        unit.is_active = active
        unit.save(update_fields=("is_active", "updated_at"))
        BusinessUnitEvent.objects.create(
            company=unit.company, business_unit=unit, actor=actor,
            kind="activated" if active else "deactivated",
        )
    return unit
