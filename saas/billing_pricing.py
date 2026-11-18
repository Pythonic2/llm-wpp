"""Versioned subscription prices for new checkouts and an administrative audit trail."""

from .models import BillingCheckout, PlanPriceRevision


PRICE_FIELD_BY_KIND = {
    BillingCheckout.Kind.FIRST_COMPANY: "first_company_monthly_price",
    BillingCheckout.Kind.EXTRA_COMPANY: "extra_company_monthly_price",
    BillingCheckout.Kind.EXTRA_NUMBER: "additional_whatsapp_monthly_price",
}
PRICE_FIELDS = tuple(PRICE_FIELD_BY_KIND.values())


def plan_prices(plan):
    return {field: str(getattr(plan, field)) for field in PRICE_FIELDS}


def record_price_revision(plan, actor, reason, previous_prices):
    """Keep existing checkout amounts untouched; record changes for new purchases."""
    new_prices = plan_prices(plan)
    if previous_prices == new_prices:
        return None
    return PlanPriceRevision.objects.create(
        plan=plan, actor=actor, reason=reason.strip(),
        previous_prices=previous_prices, new_prices=new_prices,
    )
