"""Tenant-scoped commercial rules. No payment provider or fiscal operation runs here."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    CatalogItem, Contact, Conversation, Membership, Message, Order, PaymentMethod,
    Sale, SaleEvent, SaleExpense, SaleLine, SalePayment,
)
from .business_units import default_unit, resolve_unit


CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_SALE_TOTAL = Decimal("999999999999.99")
AI_WINDOW = timedelta(days=30)
SENT_STATUSES = (Message.Status.SENT, Message.Status.DELIVERED, Message.Status.READ)


def money(value, field="valor"):
    try:
        amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({field: "Informe um valor monetário válido."}) from exc
    if not amount.is_finite():
        raise ValidationError({field: "Informe um valor monetário finito."})
    return amount


def fingerprint(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _related(model, company, pk, field):
    obj = model.objects.filter(company=company, pk=pk).first()
    if obj is None:
        raise ValidationError({field: "Registro não encontrado nesta empresa."})
    return obj


def _attribution(company, conversation, sold_at):
    if conversation is None:
        return Sale.Attribution.EXTERNAL, None
    sent = Message.objects.filter(
        company=company, conversation=conversation, direction=Message.Direction.OUTBOUND,
        status__in=SENT_STATUSES, sent_at__isnull=False,
        sent_at__lte=sold_at, sent_at__gte=sold_at - AI_WINDOW,
    )
    ai = sent.filter(response_to__isnull=False, response_to__ai_status=Message.AIStatus.COMPLETED).order_by("-sent_at").first()
    if ai:
        return Sale.Attribution.AI, ai
    if sent.filter(response_to__isnull=True).exists():
        return Sale.Attribution.HUMAN, None
    return Sale.Attribution.UNKNOWN, None


def _sale_lines(company, raw_lines, business_unit):
    if not raw_lines:
        raise ValidationError({"lines": "Adicione pelo menos um produto ou serviço."})
    prepared = []
    subtotal = ZERO
    for raw in raw_lines:
        item = None
        if raw.get("catalog_item_id"):
            item = _related(CatalogItem, company, raw["catalog_item_id"], "catalog_item_id")
            if (item.business_unit_id or default_unit(company).pk) != business_unit.pk:
                raise ValidationError({"catalog_item_id": "Item pertence a outra unidade."})
            if not item.is_active:
                raise ValidationError({"catalog_item_id": "O item do catálogo está inativo."})
        name = (item.name if item else str(raw.get("name", "")).strip())
        kind = item.kind if item else raw.get("kind")
        if not name or kind not in CatalogItem.Kind.values:
            raise ValidationError({"lines": "Informe nome e tipo válidos para cada item."})
        try:
            quantity = Decimal(str(raw.get("quantity", "1")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError({"quantity": "Informe uma quantidade válida."}) from exc
        if not quantity.is_finite() or quantity <= 0 or quantity.as_tuple().exponent < -3:
            raise ValidationError({"quantity": "A quantidade deve ser positiva e ter até 3 casas decimais."})
        default_price = item.promotional_price if item and item.promotional_price is not None else (item.sale_price if item else None)
        price_input = raw.get("unit_price", default_price)
        if price_input is None:
            raise ValidationError({"unit_price": "Informe o preço negociado para este item."})
        price = money(price_input, "unit_price")
        discount = money(raw.get("discount_amount", 0), "discount_amount")
        gross = money(quantity * price)
        if gross > MAX_SALE_TOTAL:
            raise ValidationError({"lines": "Valor da linha excede o limite permitido."})
        if price < 0 or discount < 0 or discount > gross:
            raise ValidationError({"lines": "Preço e desconto precisam formar um total não negativo."})
        cost_input = raw.get("unit_cost", item.unit_cost if item else None)
        cost = money(cost_input, "unit_cost") if cost_input is not None else None
        if cost is not None and cost < 0:
            raise ValidationError({"unit_cost": "Custo não pode ser negativo."})
        line_total = gross - discount
        prepared.append({
            "catalog_item": item, "name": name, "kind": kind,
            "category_name": item.category.name if item and item.category_id else "",
            "quantity": quantity, "unit": (item.unit if item else str(raw.get("unit", "unidade")))[:40],
            "unit_price": price, "discount_amount": discount,
            "line_total": line_total, "unit_cost": cost,
        })
        subtotal += line_total
        if subtotal > MAX_SALE_TOTAL:
            raise ValidationError({"lines": "Valor da venda excede o limite permitido."})
    return prepared, money(subtotal)


def create_sale(company, actor, data, key, payload_hash):
    if not key or len(key) > 128:
        raise ValidationError({"Idempotency-Key": "Envie uma chave única de até 128 caracteres."})
    existing = Sale.objects.filter(company=company, idempotency_key=key).first()
    if existing:
        if existing.payload_hash != payload_hash:
            raise ValidationError({"Idempotency-Key": "Esta chave já foi usada com outros dados."})
        return existing, False

    contact = _related(Contact, company, data["contact_id"], "contact_id")
    conversation = None
    if data.get("conversation_id"):
        conversation = _related(Conversation, company, data["conversation_id"], "conversation_id")
        if conversation.contact_id != contact.pk:
            raise ValidationError({"conversation_id": "A conversa não pertence ao contato."})
    order = None
    if data.get("order_id"):
        order = _related(Order, company, data["order_id"], "order_id")
        if order.status != Order.Status.APPROVED:
            raise ValidationError({"order_id": "Apenas pedidos aprovados podem ser vinculados."})
        if conversation and order.conversation_id != conversation.pk:
            raise ValidationError({"order_id": "O pedido não pertence à conversa."})
        conversation = order.conversation
        if conversation.contact_id != contact.pk:
            raise ValidationError({"order_id": "O pedido não pertence ao contato."})
    if conversation:
        business_unit = conversation.business_unit or default_unit(company)
        if data.get("business_unit_id") and str(business_unit.pk) != str(data["business_unit_id"]):
            raise ValidationError({"business_unit_id": "A unidade deve ser a da conversa."})
    else:
        business_unit = resolve_unit(company, data.get("business_unit_id"), require_explicit=True)
    responsible = actor
    if data.get("responsible_id"):
        user_id = data["responsible_id"]
        membership = Membership.objects.filter(company=company, user_id=user_id).select_related("user").first()
        if membership is None:
            raise ValidationError({"responsible_id": "O responsável não pertence à empresa."})
        responsible = membership.user
    elif conversation and conversation.assigned_to_id:
        responsible = conversation.assigned_to

    sold_at = data["sold_at"]
    if timezone.is_naive(sold_at) or sold_at > timezone.now() + timedelta(minutes=1):
        raise ValidationError({"sold_at": "Informe data/hora válida, sem data futura."})
    lines, subtotal = _sale_lines(company, data["lines"], business_unit)
    discount = money(data.get("discount_amount", 0), "discount_amount")
    if discount < 0 or discount > subtotal:
        raise ValidationError({"discount_amount": "O desconto deve ficar entre zero e o subtotal."})
    total = subtotal - discount
    expenses = data.get("expenses", [])
    prepared_expenses = []
    for entry in expenses:
        value = money(entry.get("amount"), "expense_amount")
        name = str(entry.get("name", "")).strip()
        if not name or value < 0:
            raise ValidationError({"expenses": "Informe nome e valor não negativo para cada despesa."})
        prepared_expenses.append((name[:160], value))
    attribution, evidence = _attribution(company, conversation, sold_at)
    tax_percent = company.estimated_tax_percent
    tax_amount = money(total * tax_percent / 100) if tax_percent is not None else None
    source = str(data.get("external_source", "")).strip()[:80]
    reference = str(data.get("external_reference", "")).strip()[:120]
    if reference and not source:
        raise ValidationError({"external_source": "Informe a origem da referência externa."})
    name = f"{responsible.first_name} {responsible.last_name}".strip() or responsible.username
    try:
        with transaction.atomic():
            sale = Sale.objects.create(
                company=company, business_unit=business_unit, contact=contact, conversation=conversation, order=order,
                responsible=responsible, responsible_name=name, confirmed_by=actor,
                sold_at=sold_at, subtotal=subtotal, discount_amount=discount, total=total,
                estimated_tax_percent=tax_percent, estimated_tax_amount=tax_amount,
                expenses_complete=bool(data.get("expenses_complete", False)),
                attribution=attribution, ai_evidence_message=evidence,
                external_source=source, external_reference=reference,
                idempotency_key=key, payload_hash=payload_hash,
            )
            SaleLine.objects.bulk_create(SaleLine(sale=sale, **entry) for entry in lines)
            SaleExpense.objects.bulk_create(SaleExpense(sale=sale, name=n, amount=a) for n, a in prepared_expenses)
            SaleEvent.objects.create(sale=sale, actor=actor, kind="confirmed", details={"source": "manual"})
            return sale, True
    except IntegrityError as exc:
        existing = Sale.objects.filter(company=company, idempotency_key=key).first()
        if existing:
            if existing.payload_hash == payload_hash:
                return existing, False
            raise ValidationError({"Idempotency-Key": "Esta chave já foi usada com outros dados."}) from exc
        raise ValidationError({"detail": "Já existe uma venda ativa para este pedido ou referência."}) from exc


def cancel_sale(sale, actor, reason):
    reason = str(reason).strip()
    if not reason:
        raise ValidationError({"reason": "Informe o motivo do cancelamento."})
    with transaction.atomic():
        sale = Sale.objects.select_for_update().get(pk=sale.pk, company=sale.company)
        if sale.status != Sale.Status.CONFIRMED:
            raise ValidationError({"detail": "Esta venda já foi cancelada."})
        sale.status = Sale.Status.CANCELLED
        sale.cancelled_at = timezone.now()
        sale.cancelled_by = actor
        sale.cancellation_reason = reason[:2000]
        sale.save(update_fields=("status", "cancelled_at", "cancelled_by", "cancellation_reason", "updated_at"))
        SaleEvent.objects.create(sale=sale, actor=actor, kind="cancelled", details={"reason": sale.cancellation_reason})
    return sale


def record_payment(sale, actor, data, key, payload_hash):
    if not key or len(key) > 128:
        raise ValidationError({"Idempotency-Key": "Envie uma chave única de até 128 caracteres."})
    with transaction.atomic():
        sale = Sale.objects.select_for_update().get(pk=sale.pk, company=sale.company)
        existing = SalePayment.objects.filter(sale=sale, idempotency_key=key).first()
        if existing:
            if existing.payload_hash != payload_hash:
                raise ValidationError({"Idempotency-Key": "Esta chave já foi usada com outros dados."})
            return existing, False
        amount = money(data["amount"])
        if amount <= 0:
            raise ValidationError({"amount": "Informe um valor positivo."})
        occurred_at = data["occurred_at"]
        if timezone.is_naive(occurred_at) or occurred_at > timezone.now() + timedelta(minutes=1):
            raise ValidationError({"occurred_at": "Informe data/hora válida, sem data futura."})
        kind = data["kind"]
        payments = list(SalePayment.objects.filter(sale=sale))
        received = sum((p.amount for p in payments if p.kind == SalePayment.Kind.RECEIPT), ZERO)
        refunded = sum((p.amount for p in payments if p.kind == SalePayment.Kind.REFUND), ZERO)
        method = None
        related = None
        fee_percent = fee_fixed = fee_amount = None
        if kind == SalePayment.Kind.RECEIPT:
            if sale.status != Sale.Status.CONFIRMED or received - refunded + amount > sale.total:
                raise ValidationError({"amount": "Recebimento excede o saldo da venda ativa."})
            if data.get("method_id"):
                method = _related(PaymentMethod, sale.company, data["method_id"], "method_id")
                if not method.is_active:
                    raise ValidationError({"method_id": "O meio de pagamento está inativo."})
                fee_percent, fee_fixed = method.percent_fee, method.fixed_fee
                if fee_percent is not None or fee_fixed is not None:
                    fee_amount = money(amount * (fee_percent or ZERO) / 100 + (fee_fixed or ZERO))
        elif kind == SalePayment.Kind.REFUND:
            related_id = data.get("related_receipt_id")
            related = SalePayment.objects.filter(sale=sale, pk=related_id, kind=SalePayment.Kind.RECEIPT).first()
            if related is None:
                raise ValidationError({"related_receipt_id": "Selecione um recebimento desta venda."})
            if occurred_at < related.occurred_at:
                raise ValidationError({"occurred_at": "O reembolso deve ocorrer após o recebimento."})
            already_refunded = sum((p.amount for p in payments if p.kind == SalePayment.Kind.REFUND and p.related_receipt_id == related.pk), ZERO)
            if already_refunded + amount > related.amount:
                raise ValidationError({"amount": "Reembolso excede o valor desse recebimento."})
            method = related.method
        else:
            raise ValidationError({"kind": "Tipo de lançamento inválido."})
        try:
            with transaction.atomic():
                payment = SalePayment.objects.create(
                    sale=sale, kind=kind, amount=amount, occurred_at=occurred_at,
                    method=method, method_name=method.name if method else (related.method_name if related else ""),
                    fee_percent=fee_percent, fee_fixed=fee_fixed, fee_amount=fee_amount,
                    related_receipt=related, created_by=actor, idempotency_key=key,
                    payload_hash=payload_hash,
                )
                SaleEvent.objects.create(sale=sale, actor=actor, kind=kind, details={"payment_id": str(payment.pk)})
                return payment, True
        except IntegrityError as exc:
            existing = SalePayment.objects.filter(sale=sale, idempotency_key=key).first()
            if existing and existing.payload_hash == payload_hash:
                return existing, False
            raise ValidationError({"Idempotency-Key": "Conflito ao registrar lançamento."}) from exc


def correct_attribution(sale, actor, attribution, reason, evidence_id=None):
    reason = str(reason).strip()
    if not reason:
        raise ValidationError({"reason": "Informe o motivo da correção."})
    if attribution not in Sale.Attribution.values:
        raise ValidationError({"attribution": "Classificação inválida."})
    with transaction.atomic():
        sale = Sale.objects.select_for_update().get(pk=sale.pk, company=sale.company)
        evidence = None
        if attribution == Sale.Attribution.AI:
            evidence = Message.objects.filter(
                company=sale.company, conversation=sale.conversation,
                pk=evidence_id, direction=Message.Direction.OUTBOUND,
                response_to__isnull=False, response_to__ai_status=Message.AIStatus.COMPLETED,
                status__in=SENT_STATUSES, sent_at__lte=sale.sold_at,
                sent_at__gte=sale.sold_at - AI_WINDOW,
            ).first()
            if evidence is None:
                raise ValidationError({"evidence_message_id": "Selecione uma resposta real da IA anterior à venda."})
        previous = sale.attribution
        sale.attribution = attribution
        sale.ai_evidence_message = evidence
        sale.attribution_note = reason[:300]
        sale.save(update_fields=("attribution", "ai_evidence_message", "attribution_note", "updated_at"))
        SaleEvent.objects.create(sale=sale, actor=actor, kind="attribution_corrected", details={"from": previous, "to": attribution, "reason": reason[:1000], "evidence_message_id": evidence.pk if evidence else None})
    return sale


def sale_financials(sale):
    """Unknown inputs remain None; partial totals are never called net profit."""
    lines = list(sale.lines.all())
    payments = list(sale.payments.all())
    expenses = list(sale.expenses.all())
    line_costs_known = all(line.unit_cost is not None for line in lines)
    product_cost = money(sum((line.quantity * line.unit_cost for line in lines), ZERO)) if line_costs_known else None
    extra_cost = money(sum((entry.amount for entry in expenses), ZERO)) if sale.expenses_complete else None
    receipts = sum((p.amount for p in payments if p.kind == SalePayment.Kind.RECEIPT), ZERO)
    refunds = sum((p.amount for p in payments if p.kind == SalePayment.Kind.REFUND), ZERO)
    fee_known = (
        bool(payments)
        and money(receipts - refunds) == sale.total
        and not any(p.kind == SalePayment.Kind.REFUND for p in payments)
        and all(p.fee_amount is not None for p in payments if p.kind == SalePayment.Kind.RECEIPT)
    )
    fee_total = money(sum((p.fee_amount for p in payments if p.fee_amount is not None), ZERO)) if fee_known else None
    known_costs = money(sum((part for part in (product_cost, extra_cost, fee_total, sale.estimated_tax_amount) if part is not None), ZERO))
    complete = all(part is not None for part in (product_cost, extra_cost, fee_total, sale.estimated_tax_amount))
    return {
        "received_net": str(money(receipts - refunds)),
        "outstanding": str(money(max(sale.total - (receipts - refunds), ZERO))),
        "item_cost": str(product_cost) if product_cost is not None else None,
        "extra_cost": str(extra_cost) if extra_cost is not None else None,
        "payment_fees": str(fee_total) if fee_total is not None else None,
        "estimated_tax": str(sale.estimated_tax_amount) if sale.estimated_tax_amount is not None else None,
        "known_costs": str(known_costs),
        "estimated_result": str(money(sale.total - known_costs)) if complete else None,
        "cost_data": "complete" if complete else "partial" if any(part is not None for part in (product_cost, extra_cost, fee_total, sale.estimated_tax_amount)) else "absent",
    }
