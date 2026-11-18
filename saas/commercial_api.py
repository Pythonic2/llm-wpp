"""Catalog, sales and financial reporting API scoped to one company."""

import csv
from datetime import date, datetime, timedelta, time
from decimal import Decimal
from io import StringIO

from django.db.models import Count, Min, Sum, Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .api import _company_for_request
from .business_units import resolve_unit
from .commercial import (
    ZERO, cancel_sale, correct_attribution, create_sale, fingerprint, money,
    record_payment, sale_financials,
)
from .models import (
    BusinessUnitEvent, CatalogCategory, CatalogItem, Contact, Conversation, MediaAsset, Message,
    PaymentMethod, Sale, SaleEvent, SaleExpense, SaleLine, SalePayment,
)


class CommercialPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class CatalogCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = CatalogCategory
        fields = ("id", "name", "is_active", "created_at")
        read_only_fields = ("id", "created_at")


class CatalogItemSerializer(serializers.ModelSerializer):
    business_unit_id = serializers.UUIDField(read_only=True)
    media_assets = serializers.PrimaryKeyRelatedField(queryset=MediaAsset.objects.all(), many=True, required=False)

    class Meta:
        model = CatalogItem
        fields = (
            "id", "business_unit_id", "name", "kind", "category", "description", "price_mode", "sale_price",
            "promotional_price", "unit_cost", "unit", "billing_unit", "sku", "availability",
            "duration_minutes", "variants", "internal_notes", "media_assets", "is_active",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        company = self.context["company"]
        category = attrs.get("category", getattr(self.instance, "category", None))
        if category and category.company_id != company.pk:
            raise serializers.ValidationError({"category": "Categoria pertence a outra empresa."})
        assets = attrs.get("media_assets")
        if assets is None and self.instance is not None:
            assets = self.instance.media_assets.all()
        if any(asset.company_id != company.pk for asset in (assets or [])):
            raise serializers.ValidationError({"media_assets": "Mídia pertence a outra empresa."})
        unit = self.context.get("business_unit")
        if unit and any(asset.business_unit_id != unit.pk for asset in (assets or [])):
            raise serializers.ValidationError({"media_assets": "Mídia pertence a outra unidade."})
        mode = attrs.get("price_mode", getattr(self.instance, "price_mode", CatalogItem.PriceMode.QUOTE))
        price = attrs.get("sale_price", getattr(self.instance, "sale_price", None))
        if mode in {CatalogItem.PriceMode.FIXED, CatalogItem.PriceMode.FROM} and price is None:
            raise serializers.ValidationError({"sale_price": "Informe o preço para este modo."})
        promotion = attrs.get("promotional_price", getattr(self.instance, "promotional_price", None))
        if promotion is not None and (mode == CatalogItem.PriceMode.QUOTE or price is None or promotion > price):
            raise serializers.ValidationError({"promotional_price": "A promoção exige preço público e não pode superá-lo."})
        for name in ("sale_price", "promotional_price", "unit_cost"):
            value = attrs.get(name, getattr(self.instance, name, None))
            if value is not None and value < 0:
                raise serializers.ValidationError({name: "Informe valor não negativo."})
        return attrs


class PaymentMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = ("id", "name", "kind", "condition_label", "percent_fee", "fixed_fee", "is_active", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        percent = attrs.get("percent_fee", getattr(self.instance, "percent_fee", None))
        fixed = attrs.get("fixed_fee", getattr(self.instance, "fixed_fee", None))
        if percent is not None and not 0 <= percent <= 100:
            raise serializers.ValidationError({"percent_fee": "Informe percentual entre 0 e 100."})
        if fixed is not None and fixed < 0:
            raise serializers.ValidationError({"fixed_fee": "Informe valor não negativo."})
        return attrs


class SaleLineInputSerializer(serializers.Serializer):
    catalog_item_id = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    kind = serializers.ChoiceField(choices=CatalogItem.Kind.choices, required=False)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal("0.001"))
    unit = serializers.CharField(max_length=40, required=False, allow_blank=True)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=ZERO, required=False, allow_null=True)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=ZERO, required=False, default=ZERO)
    unit_cost = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=ZERO, required=False, allow_null=True)

    def validate(self, attrs):
        if not attrs.get("catalog_item_id") and (not attrs.get("name") or not attrs.get("kind")):
            raise serializers.ValidationError("Informe item do catálogo ou nome e tipo avulsos.")
        return attrs


class SaleExpenseInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=ZERO)


class SaleInputSerializer(serializers.Serializer):
    contact_id = serializers.IntegerField(min_value=1)
    conversation_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    order_id = serializers.UUIDField(required=False, allow_null=True)
    business_unit_id = serializers.UUIDField(required=False, allow_null=True)
    responsible_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    sold_at = serializers.DateTimeField()
    discount_amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=ZERO, required=False, default=ZERO)
    lines = SaleLineInputSerializer(many=True, allow_empty=False)
    expenses = SaleExpenseInputSerializer(many=True, required=False, default=list)
    expenses_complete = serializers.BooleanField(required=False, default=False)
    external_source = serializers.CharField(max_length=80, required=False, allow_blank=True)
    external_reference = serializers.CharField(max_length=120, required=False, allow_blank=True)


class SaleLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleLine
        fields = ("id", "catalog_item", "name", "kind", "category_name", "quantity", "unit", "unit_price", "discount_amount", "line_total", "unit_cost")


class SaleExpenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleExpense
        fields = ("id", "name", "amount")


class SalePaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalePayment
        fields = ("id", "kind", "amount", "occurred_at", "method", "method_name", "fee_percent", "fee_fixed", "fee_amount", "related_receipt", "created_at")


class SaleEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleEvent
        fields = ("id", "actor", "kind", "details", "created_at")


class SaleSerializer(serializers.ModelSerializer):
    business_unit_id = serializers.UUIDField(read_only=True)
    lines = SaleLineSerializer(many=True, read_only=True)
    expenses = SaleExpenseSerializer(many=True, read_only=True)
    payments = SalePaymentSerializer(many=True, read_only=True)
    events = SaleEventSerializer(many=True, read_only=True)
    financials = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = (
            "id", "business_unit_id", "contact", "conversation", "order", "responsible", "responsible_name",
            "confirmed_by", "sold_at", "confirmed_at", "subtotal", "discount_amount", "total",
            "estimated_tax_percent", "estimated_tax_amount", "expenses_complete", "status",
            "cancelled_at", "cancelled_by", "cancellation_reason", "attribution",
            "ai_evidence_message", "attribution_version", "attribution_note", "external_source",
            "external_reference", "replaces_sale", "lines", "expenses", "payments", "financials",
            "events", "created_at", "updated_at",
        )

    def get_financials(self, obj):
        return sale_financials(obj)


class PaymentInputSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=SalePayment.Kind.choices)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    occurred_at = serializers.DateTimeField()
    method_id = serializers.UUIDField(required=False, allow_null=True)
    related_receipt_id = serializers.UUIDField(required=False, allow_null=True)


class CatalogCategoryListView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id):
        company, _ = _company_for_request(request, company_id)
        items = CatalogCategory.objects.filter(company=company).order_by("name")
        return Response(CatalogCategorySerializer(items, many=True).data)

    def post(self, request, company_id):
        company, _ = _company_for_request(request, company_id, "admin")
        serializer = CatalogCategorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(company=company)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class CatalogCategoryDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def patch(self, request, company_id, category_id):
        company, _ = _company_for_request(request, company_id, "admin")
        obj = CatalogCategory.objects.filter(company=company, pk=category_id).first()
        if obj is None:
            raise ValidationError({"category_id": "Categoria não encontrada."})
        serializer = CatalogCategorySerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class CatalogItemListView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id):
        company, membership = _company_for_request(request, company_id)
        items = CatalogItem.objects.filter(company=company).select_related("category").prefetch_related("media_assets")
        if request.query_params.get("business_unit_id"):
            unit = resolve_unit(company, request.query_params["business_unit_id"], active=False)
            items = items.filter(business_unit=unit)
        if membership and membership.role == "agent":
            items = items.filter(is_active=True)
        if request.query_params.get("kind"):
            items = items.filter(kind=request.query_params["kind"])
        if request.query_params.get("category"):
            items = items.filter(category_id=request.query_params["category"])
        if request.query_params.get("search"):
            items = items.filter(Q(name__icontains=request.query_params["search"]) | Q(description__icontains=request.query_params["search"]))
        paginator = CommercialPagination()
        page = paginator.paginate_queryset(items.order_by("name", "pk"), request, view=self)
        data = CatalogItemSerializer(page, many=True).data
        if membership and membership.role == "agent":
            for item in data:
                item.pop("unit_cost", None)
                item.pop("internal_notes", None)
        return paginator.get_paginated_response(data)

    def post(self, request, company_id):
        company, _ = _company_for_request(request, company_id, "admin")
        unit = resolve_unit(company, request.data.get("business_unit_id"))
        serializer = CatalogItemSerializer(data=request.data, context={"company": company, "business_unit": unit})
        serializer.is_valid(raise_exception=True)
        serializer.save(company=company, business_unit=unit)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class CatalogItemDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id, item_id):
        company, membership = _company_for_request(request, company_id)
        obj = CatalogItem.objects.filter(company=company, pk=item_id).first()
        if obj is None or (membership and membership.role == "agent" and not obj.is_active):
            raise ValidationError({"item_id": "Item não encontrado."})
        data = CatalogItemSerializer(obj).data
        if membership and membership.role == "agent":
            data.pop("unit_cost", None)
            data.pop("internal_notes", None)
        return Response(data)

    def patch(self, request, company_id, item_id):
        company, _ = _company_for_request(request, company_id, "admin")
        obj = CatalogItem.objects.filter(company=company, pk=item_id).first()
        if obj is None:
            raise ValidationError({"item_id": "Item não encontrado."})
        unit = resolve_unit(company, request.data["business_unit_id"]) if "business_unit_id" in request.data else None
        serializer = CatalogItemSerializer(obj, data=request.data, partial=True, context={"company": company, "business_unit": unit or obj.business_unit})
        serializer.is_valid(raise_exception=True)
        if unit:
            serializer.save(business_unit=unit)
            BusinessUnitEvent.objects.create(
                company=company, business_unit=unit, actor=request.user,
                kind="catalog_item_moved", details={"item_id": str(obj.pk)},
            )
        else:
            serializer.save()
        return Response(serializer.data)


class PaymentMethodListView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id):
        company, _ = _company_for_request(request, company_id, "admin")
        return Response(PaymentMethodSerializer(PaymentMethod.objects.filter(company=company).order_by("name"), many=True).data)

    def post(self, request, company_id):
        company, _ = _company_for_request(request, company_id, "admin")
        serializer = PaymentMethodSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(company=company)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class PaymentMethodDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def patch(self, request, company_id, method_id):
        company, _ = _company_for_request(request, company_id, "admin")
        obj = PaymentMethod.objects.filter(company=company, pk=method_id).first()
        if obj is None:
            raise ValidationError({"method_id": "Meio de pagamento não encontrado."})
        serializer = PaymentMethodSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


def _sale_queryset(company):
    return Sale.objects.filter(company=company).select_related(
        "contact", "conversation", "order", "responsible", "confirmed_by",
    ).prefetch_related("lines", "expenses", "payments", "events")


def _sale_or_error(company, sale_id):
    sale = _sale_queryset(company).filter(pk=sale_id).first()
    if sale is None:
        raise ValidationError({"sale_id": "Venda não encontrada nesta empresa."})
    return sale


def _date_range(request):
    today = timezone.localdate()
    try:
        start_date = date.fromisoformat(request.query_params.get("from", today.replace(day=1).isoformat()))
        end_date = date.fromisoformat(request.query_params.get("to", today.isoformat()))
    except ValueError as exc:
        raise ValidationError({"period": "Use datas no formato AAAA-MM-DD."}) from exc
    if end_date < start_date or (end_date - start_date).days > 366:
        raise ValidationError({"period": "Selecione até 366 dias em ordem cronológica."})
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz)
    return start_date, end_date, start, end


def _filter_sales(request, company, *, include_sale_dates=True):
    items = _sale_queryset(company)
    if request.query_params.get("business_unit_id"):
        unit = resolve_unit(company, request.query_params["business_unit_id"], active=False)
        items = items.filter(business_unit=unit)
    status_filter = request.query_params.get("status")
    if status_filter:
        if status_filter not in Sale.Status.values:
            raise ValidationError({"status": "Situação inválida."})
        items = items.filter(status=status_filter)
    if request.query_params.get("responsible_id"):
        items = items.filter(responsible_id=request.query_params["responsible_id"])
    if request.query_params.get("attribution"):
        if request.query_params["attribution"] not in Sale.Attribution.values:
            raise ValidationError({"attribution": "Origem inválida."})
        items = items.filter(attribution=request.query_params["attribution"])
    if request.query_params.get("kind"):
        if request.query_params["kind"] not in CatalogItem.Kind.values:
            raise ValidationError({"kind": "Tipo de item inválido."})
        items = items.filter(lines__kind=request.query_params["kind"]).distinct()
    if request.query_params.get("item_id"):
        items = items.filter(lines__catalog_item_id=request.query_params["item_id"]).distinct()
    if request.query_params.get("payment_method_id"):
        items = items.filter(payments__method_id=request.query_params["payment_method_id"]).distinct()
    if request.query_params.get("contact_id"):
        items = items.filter(contact_id=request.query_params["contact_id"])
    if include_sale_dates and (request.query_params.get("from") or request.query_params.get("to")):
        _, _, start, end = _date_range(request)
        items = items.filter(sold_at__gte=start, sold_at__lt=end)
    return items


class SaleListView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id):
        company, _ = _company_for_request(request, company_id, "admin")
        paginator = CommercialPagination()
        page = paginator.paginate_queryset(_filter_sales(request, company).order_by("-sold_at", "-created_at"), request, view=self)
        return paginator.get_paginated_response(SaleSerializer(page, many=True).data)

    def post(self, request, company_id):
        company, _ = _company_for_request(request, company_id, "admin")
        serializer = SaleInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale, created = create_sale(
            company, request.user, serializer.validated_data,
            request.headers.get("Idempotency-Key", "").strip(), fingerprint(request.data),
        )
        sale = _sale_or_error(company, sale.pk)
        return Response(SaleSerializer(sale).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class SaleDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id, sale_id):
        company, _ = _company_for_request(request, company_id, "admin")
        return Response(SaleSerializer(_sale_or_error(company, sale_id)).data)


class SaleCancelView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request, company_id, sale_id):
        company, _ = _company_for_request(request, company_id, "admin")
        sale = cancel_sale(_sale_or_error(company, sale_id), request.user, request.data.get("reason", ""))
        return Response(SaleSerializer(_sale_or_error(company, sale.pk)).data)


class SaleAttributionView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request, company_id, sale_id):
        company, _ = _company_for_request(request, company_id, "admin")
        sale = correct_attribution(
            _sale_or_error(company, sale_id), request.user,
            request.data.get("attribution", ""), request.data.get("reason", ""),
            request.data.get("evidence_message_id"),
        )
        return Response(SaleSerializer(_sale_or_error(company, sale.pk)).data)


class SalePaymentListView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id, sale_id):
        company, _ = _company_for_request(request, company_id, "admin")
        sale = _sale_or_error(company, sale_id)
        return Response(SalePaymentSerializer(sale.payments.all().order_by("occurred_at", "pk"), many=True).data)

    def post(self, request, company_id, sale_id):
        company, _ = _company_for_request(request, company_id, "admin")
        sale = _sale_or_error(company, sale_id)
        serializer = PaymentInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment, created = record_payment(
            sale, request.user, serializer.validated_data,
            request.headers.get("Idempotency-Key", "").strip(), fingerprint(request.data),
        )
        return Response(SalePaymentSerializer(payment).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


def _bucket_date(value, interval):
    day = timezone.localtime(value).date()
    if interval == "weekly":
        return day - timedelta(days=day.weekday())
    if interval == "monthly":
        return day.replace(day=1)
    return day


def _add_breakdown(bucket, label, amount, quantity=1):
    row = bucket.setdefault(label or "Sem categoria", {"label": label or "Sem categoria", "sales": 0, "amount": ZERO, "quantity": ZERO})
    row["sales"] += 1
    row["amount"] += amount
    row["quantity"] += Decimal(str(quantity))


def _breakdown_rows(bucket):
    return [
        {"label": row["label"], "sales": row["sales"], "amount": str(money(row["amount"])), "quantity": str(row["quantity"])}
        for row in sorted(bucket.values(), key=lambda item: item["amount"], reverse=True)
    ]


class FinanceDashboardView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id):
        company, _ = _company_for_request(request, company_id, "admin")
        start_date, end_date, start, end = _date_range(request)
        interval = request.query_params.get("interval", "daily")
        if interval not in {"daily", "weekly", "monthly"}:
            raise ValidationError({"interval": "Use daily, weekly ou monthly."})
        sales = list(_filter_sales(request, company, include_sale_dates=False).filter(
            status=Sale.Status.CONFIRMED, sold_at__gte=start, sold_at__lt=end,
        ))
        count = len(sales)
        total = money(sum((sale.total for sale in sales), ZERO))
        assisted = [sale for sale in sales if sale.attribution == Sale.Attribution.AI]
        assisted_total = money(sum((sale.total for sale in assisted), ZERO))
        cancelled = _filter_sales(request, company, include_sale_dates=False).filter(
            status=Sale.Status.CANCELLED, cancelled_at__gte=start, cancelled_at__lt=end,
        )
        payment_sale_ids = _filter_sales(request, company, include_sale_dates=False).values("pk")
        payments = list(SalePayment.objects.filter(sale_id__in=payment_sale_ids, occurred_at__gte=start, occurred_at__lt=end).select_related("method"))
        receipts = sum((p.amount for p in payments if p.kind == SalePayment.Kind.RECEIPT), ZERO)
        refunds = sum((p.amount for p in payments if p.kind == SalePayment.Kind.REFUND), ZERO)
        timeline = {}
        by_kind, by_category, by_item, by_attendant, by_method = {}, {}, {}, {}, {}
        cost_coverage = {"complete": 0, "partial": 0, "absent": 0}
        known_costs = ZERO
        complete_result = ZERO
        all_complete = bool(sales)
        all_item_costs_known = bool(sales)
        item_costs = ZERO
        known_fees = known_taxes = ZERO
        has_fees = has_taxes = False
        for sale in sales:
            bucket = _bucket_date(sale.sold_at, interval).isoformat()
            _add_breakdown(timeline, bucket, sale.total)
            _add_breakdown(by_attendant, sale.responsible_name or "Sem responsável", sale.total)
            financial = sale_financials(sale)
            cost_coverage[financial["cost_data"]] += 1
            known_costs += Decimal(financial["known_costs"])
            if financial["estimated_result"] is None:
                all_complete = False
            else:
                complete_result += Decimal(financial["estimated_result"])
            if financial["item_cost"] is None:
                all_item_costs_known = False
            else:
                item_costs += Decimal(financial["item_cost"])
            if financial["payment_fees"] is not None:
                known_fees += Decimal(financial["payment_fees"])
                has_fees = True
            if financial["estimated_tax"] is not None:
                known_taxes += Decimal(financial["estimated_tax"])
                has_taxes = True
            lines = list(sale.lines.all())
            remaining = sale.total
            for index, line in enumerate(lines):
                # Allocate the sale-level discount so category/item sums equal the recorded sale total.
                amount = remaining if index == len(lines) - 1 else (
                    money(sale.total * line.line_total / sale.subtotal) if sale.subtotal else ZERO
                )
                remaining -= amount
                _add_breakdown(by_kind, line.get_kind_display(), amount, line.quantity)
                _add_breakdown(by_category, line.category_name or "Sem categoria", amount, line.quantity)
                _add_breakdown(by_item, line.name, amount, line.quantity)
        for payment in payments:
            amount = payment.amount if payment.kind == SalePayment.Kind.RECEIPT else -payment.amount
            _add_breakdown(by_method, payment.method_name or "Não informado", amount)
        period_length = end - start
        previous_total = _filter_sales(request, company, include_sale_dates=False).filter(
            status=Sale.Status.CONFIRMED,
            sold_at__gte=start - period_length, sold_at__lt=start,
        ).aggregate(value=Sum("total"))["value"] or ZERO

        cohort_query = Conversation.objects.filter(
            company=company, first_eligible_inbound_at__gte=start,
            first_eligible_inbound_at__lt=end,
        )
        if request.query_params.get("business_unit_id"):
            unit = resolve_unit(company, request.query_params["business_unit_id"], active=False)
            cohort_query = cohort_query.filter(business_unit=unit)
        cohort = list(cohort_query.values_list("pk", "first_eligible_inbound_at", "opportunity_status"))
        first_by_conversation = {pk: first_at for pk, first_at, _ in cohort}
        cohort_sales = Sale.objects.filter(
            company=company, status=Sale.Status.CONFIRMED,
            conversation_id__in=first_by_conversation,
        ).values_list("conversation_id", "sold_at")
        converted = {
            conversation_id for conversation_id, sold_at in cohort_sales
            if first_by_conversation[conversation_id] <= sold_at <= first_by_conversation[conversation_id] + timedelta(days=30)
        }
        denominator = len(cohort)
        conversion = money(Decimal(len(converted)) * 100 / denominator) if denominator else None
        return Response({
            "period": {"from": start_date.isoformat(), "to": end_date.isoformat(), "interval": interval},
            "confirmed_sales": count, "registered_revenue": str(total),
            "average_ticket": str(money(total / count)) if count else "0.00",
            "received_net": str(money(receipts - refunds)),
            "ai_assisted_sales": len(assisted), "ai_assisted_revenue": str(assisted_total),
            "cancelled_sales": cancelled.count(),
            "cancelled_value": str(money(cancelled.aggregate(value=Sum("total"))["value"] or ZERO)),
            "refunds": str(money(refunds)),
            "conversion": {"rate_percent": str(conversion) if conversion is not None else None,
                           "converted": len(converted), "eligible": denominator,
                           "window_days": 30,
                           "cohort_complete": all(first_at + timedelta(days=30) <= timezone.now() for _, first_at, _ in cohort)},
            "opportunity_funnel": {
                "eligible_conversations": denominator,
                "currently_won": sum(state == "won" for _, _, state in cohort),
                "currently_lost": sum(state == "lost" for _, _, state in cohort),
                "confirmed_within_30_days": len(converted),
            },
            "financial_coverage": cost_coverage,
            "known_costs": str(money(known_costs)) if any(cost_coverage[key] for key in ("complete", "partial")) else None,
            "payment_fees": str(money(known_fees)) if has_fees else None,
            "estimated_taxes": str(money(known_taxes)) if has_taxes else None,
            "gross_margin_estimate": str(money(total - item_costs)) if all_item_costs_known else None,
            "estimated_result": str(money(complete_result)) if all_complete else None,
            "previous_period_revenue": str(money(previous_total)),
            "revenue_change_percent": str(money((total - previous_total) * 100 / previous_total)) if previous_total else None,
            "timeline": _breakdown_rows(timeline), "by_kind": _breakdown_rows(by_kind),
            "by_category": _breakdown_rows(by_category), "by_item": _breakdown_rows(by_item)[:20],
            "by_attendant": _breakdown_rows(by_attendant), "by_payment_method": _breakdown_rows(by_method),
        })


def _safe_csv(value):
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


class SaleReportCSVView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id):
        company, _ = _company_for_request(request, company_id, "admin")
        report = request.query_params.get("group_by", "sales")
        if report not in {"sales", "items", "payments", "category", "attendant", "attribution"}:
            raise ValidationError({"group_by": "Relatório inválido."})
        sales = list(_filter_sales(request, company, include_sale_dates=report != "payments").order_by("sold_at", "pk")[:10001])
        if len(sales) > 10000:
            raise ValidationError({"period": "Relatório muito grande; reduza o período."})
        output = StringIO()
        output.write("\ufeff")
        writer = csv.writer(output, delimiter=";")

        def row(*values):
            writer.writerow([_safe_csv(value) for value in values])

        if report == "sales":
            row("Venda", "Data", "Contato", "Situação", "Responsável", "Origem", "Valor", "Recebido líquido", "Pedido")
            for sale in sales:
                row(sale.pk, timezone.localtime(sale.sold_at).isoformat(), sale.contact.name or sale.contact.phone,
                    sale.status, sale.responsible_name, sale.attribution, sale.total,
                    sale_financials(sale)["received_net"], sale.order_id)
        elif report == "items":
            row("Venda", "Data", "Tipo", "Categoria", "Item", "Quantidade", "Unidade", "Preço unitário", "Desconto", "Total")
            for sale in sales:
                for line in sale.lines.all():
                    row(sale.pk, timezone.localtime(sale.sold_at).isoformat(), line.kind, line.category_name,
                        line.name, line.quantity, line.unit, line.unit_price, line.discount_amount, line.line_total)
        elif report == "payments":
            row("Venda", "Data", "Tipo", "Meio", "Valor", "Taxa estimada")
            _, _, period_start, period_end = _date_range(request)
            for sale in sales:
                for payment in sale.payments.all():
                    if not period_start <= payment.occurred_at < period_end:
                        continue
                    row(sale.pk, timezone.localtime(payment.occurred_at).isoformat(), payment.kind,
                        payment.method_name, payment.amount, payment.fee_amount)
        else:
            buckets = {}
            for sale in sales:
                if sale.status != Sale.Status.CONFIRMED:
                    continue
                if report == "category":
                    lines = list(sale.lines.all())
                    remaining = sale.total
                    for index, line in enumerate(lines):
                        amount = remaining if index == len(lines) - 1 else (
                            money(sale.total * line.line_total / sale.subtotal) if sale.subtotal else ZERO
                        )
                        remaining -= amount
                        _add_breakdown(buckets, line.category_name or "Sem categoria", amount, line.quantity)
                else:
                    label = sale.responsible_name if report == "attendant" else sale.attribution
                    _add_breakdown(buckets, label, sale.total)
            row("Grupo", "Registros", "Valor", "Quantidade")
            for item in _breakdown_rows(buckets):
                row(item["label"], item["sales"], item["amount"], item["quantity"])
        response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="gesttech-{report}.csv"'
        response["Cache-Control"] = "no-store"
        return response
