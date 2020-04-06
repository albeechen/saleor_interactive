"""Wishlist-related utility functions."""
from datetime import date, timedelta
from functools import wraps
from typing import Optional, Tuple
from uuid import UUID

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Min, Sum
from django.utils import timezone
from django.utils.encoding import smart_text
from django.utils.translation import get_language, pgettext, pgettext_lazy
from prices import Money, MoneyRange, TaxedMoneyRange

from ..account.forms import get_address_form
from ..account.models import Address, User
from ..account.utils import store_user_address
from ..wishlist.error_codes import WishlistErrorCode
from ..core.exceptions import InsufficientStock
from ..core.taxes import quantize_price, zero_taxed_money
from ..core.utils import to_local_currency
from ..core.utils.promo_code import (
    InvalidPromoCode,
    promo_code_is_gift_card,
    promo_code_is_voucher,
)
from ..discount import VoucherType
from ..discount.models import NotApplicable, Voucher
from ..discount.utils import (
    add_voucher_usage_by_customer,
    decrease_voucher_usage,
    get_products_voucher_discount,
    increase_voucher_usage,
    remove_voucher_usage_by_customer,
    validate_voucher_for_checkout,
)
from ..extensions.manager import get_extensions_manager
from ..giftcard.utils import (
    add_gift_card_code_to_checkout,
    remove_gift_card_code_from_checkout,
)
from ..order.actions import order_created
from ..order.emails import send_order_confirmation
from ..order.models import Order, OrderLine
from ..shipping.models import ShippingMethod
from . import AddressType, logger
from .forms import (
    AddressChoiceForm,
    AnonymousUserBillingForm,
    AnonymousUserShippingForm,
    BillingAddressChoiceForm,
)
from .models import Wishlist, WishlistLine

COOKIE_NAME = "wishlist"


def set_wishlist_cookie(simple_wishlist, response):
    """Update response with a wishlist token cookie."""
    # FIXME: document why session is not used
    max_age = int(timedelta(days=30).total_seconds())
    response.set_signed_cookie(COOKIE_NAME, simple_wishlist.token, max_age=max_age)


def contains_unavailable_variants(wishlist):
    """Return `True` if wishlist contains any unfulfillable lines."""
    try:
        for line in wishlist:
            line.variant.check_quantity(line.quantity)
    except InsufficientStock:
        return True
    return False


def token_is_valid(token):
    """Validate a wishlist token."""
    if token is None:
        return False
    if isinstance(token, UUID):
        return True
    try:
        UUID(token)
    except ValueError:
        return False
    return True


def remove_unavailable_variants(wishlist):
    """Remove any unavailable items from wishlist."""
    for line in wishlist:
        try:
            add_variant_to_wishlist(wishlist, line.variant, line.quantity, replace=True)
        except InsufficientStock as e:
            quantity = e.item.quantity_available
            add_variant_to_wishlist(wishlist, line.variant, quantity, replace=True)


def get_prices_of_discounted_specific_product(lines, voucher, discounts=None):
    """Get prices of variants belonging to the discounted specific products.

    Specific products are products, collections and categories.
    Product must be assigned directly to the discounted category, assigning
    product to child category won't work.
    """
    discounted_products = voucher.products.all()
    discounted_categories = set(voucher.categories.all())
    discounted_collections = set(voucher.collections.all())

    line_prices = []
    discounted_lines = []
    if discounted_products or discounted_collections or discounted_categories:
        for line in lines:
            line_product = line.variant.product
            line_category = line.variant.product.category
            line_collections = set(line.variant.product.collections.all())
            if line.variant and (
                line_product in discounted_products
                or line_category in discounted_categories
                or line_collections.intersection(discounted_collections)
            ):
                discounted_lines.append(line)
    else:
        # If there's no discounted products, collections or categories,
        # it means that all products are discounted
        discounted_lines.extend(list(lines))

    manager = get_extensions_manager()
    for line in discounted_lines:
        line_total = manager.calculate_wishlist_line_total(line, discounts or []).gross
        line_unit_price = quantize_price(
            (line_total / line.quantity), line_total.currency
        )
        line_prices.extend([line_unit_price] * line.quantity)

    return line_prices


def check_product_availability_and_warn(request, wishlist):
    """Warn if wishlist contains any lines that cannot be fulfilled."""
    if contains_unavailable_variants(wishlist):
        msg = pgettext_lazy(
            "Wishlist warning message",
            "Sorry. We don't have that many items in stock. "
            "Quantity was set to maximum available for now.",
        )
        messages.warning(request, msg)
        remove_unavailable_variants(wishlist)


def find_and_assign_anonymous_wishlist(queryset=Wishlist.objects.all()):
    """Assign wishlist from cookie to request user."""

    def get_wishlist(view):
        @wraps(view)
        def func(request, *args, **kwargs):
            response = view(request, *args, **kwargs)
            token = request.get_signed_cookie(COOKIE_NAME, default=None)
            if not token_is_valid(token):
                return response
            wishlist = get_anonymous_wishlist_from_token(
                token=token, wishlist_queryset=queryset
            )
            if wishlist is None:
                return response
            if request.user.is_authenticated:
                with transaction.atomic():
                    change_wishlist_user(wishlist, request.user)
                    wishlists_to_close = Wishlist.objects.filter(user=request.user)
                    wishlists_to_close = wishlists_to_close.exclude(token=token)
                    wishlists_to_close.delete()
                response.delete_cookie(COOKIE_NAME)
            return response

        return func

    return get_wishlist


def get_or_create_anonymous_wishlist_from_token(
    token, wishlist_queryset=Wishlist.objects.all()
):
    """Return an open unassigned wishlist with given token or create a new one."""
    return wishlist_queryset.filter(token=token, user=None).get_or_create(
        defaults={"user": None}
    )[0]


def get_user_wishlist(
    user: User, wishlist_queryset=Wishlist.objects.all(), auto_create=False
) -> Tuple[Optional[Wishlist], bool]:
    """Return an active wishlist for given user or None if no auto create.

    If auto create is enabled, it will retrieve an active wishlist or create it
    (safer for concurrency).
    """
    if auto_create:
        return wishlist_queryset.get_or_create(
            user=user,
            defaults={
                "shipping_address": user.default_shipping_address,
                "billing_address": user.default_billing_address,
            },
        )
    return wishlist_queryset.filter(user=user).first(), False


def get_anonymous_wishlist_from_token(token, wishlist_queryset=Wishlist.objects.all()):
    """Return an open unassigned wishlist with given token if any."""
    return wishlist_queryset.filter(token=token, user=None).first()


def get_or_create_wishlist_from_request(
    request, wishlist_queryset=Wishlist.objects.all()
) -> Wishlist:
    """Fetch wishlist from database or create a new one based on cookie."""
    if request.user.is_authenticated:
        return get_user_wishlist(request.user, wishlist_queryset, auto_create=True)[0]
    
  




def get_wishlist_from_request(request, wishlist_queryset=Wishlist.objects.all()):
    """Fetch wishlist from database or return a new instance based on cookie."""
    if request.user.is_authenticated:
        wishlist, _ = get_user_wishlist(request.user, wishlist_queryset)
        user = request.user
    else:
        token = request.get_signed_cookie(COOKIE_NAME, default=None)
        wishlist = get_anonymous_wishlist_from_token(token, wishlist_queryset)
        user = None
    if wishlist is not None:
        return wishlist
    if user:
        return Wishlist(user=user)
    return Wishlist()


def get_or_empty_db_wishlist(wishlist_queryset=Wishlist.objects.all()):
    """Decorate view to receive a wishlist if one exists.

    Changes the view signature from `func(request, ...)` to
    `func(request, wishlist, ...)`.

    If no matching wishlist is found, an unsaved `Wishlist` instance will be used.
    """
    # FIXME: behave like middleware and assign wishlist to request instead
    def get_wishlist(view):
        @wraps(view)
        def func(request, *args, **kwargs):
            wishlist = get_wishlist_from_request(request, wishlist_queryset)
            return view(request, wishlist, *args, **kwargs)

        return func

    return get_wishlist


def find_open_wishlist_for_user(user):
    """Find an open wishlist for the given user."""
    wishlists = user.wishlists.all()
    open_wishlist = wishlists.first()
    if len(wishlists) > 1:
        logger.warning("%s has more than one open basket", user)
        wishlists.exclude(token=open_wishlist.token).delete()
    return open_wishlist


def change_wishlist_user(wishlist, user):
    """Assign wishlist to a user.

    If the user already has an open wishlist assigned, cancel it.
    """
    open_wishlist = find_open_wishlist_for_user(user)
    if open_wishlist is not None:
        open_wishlist.delete()
    wishlist.user = user
    wishlist.shipping_address = user.default_shipping_address
    wishlist.billing_address = user.default_billing_address
    wishlist.save(update_fields=["user", "shipping_address", "billing_address"])


def update_wishlist_quantity(wishlist):
    """Update the total quantity in wishlist."""
    total_lines = wishlist.lines.aggregate(total_quantity=Sum("quantity"))[
        "total_quantity"
    ]
    if not total_lines:
        total_lines = 0
    wishlist.quantity = total_lines
    wishlist.save(update_fields=["quantity"])


def check_variant_in_stock(
    wishlist, variant, quantity=1, replace=False, check_quantity=True
) -> Tuple[int, Optional[WishlistLine]]:
    """Check if a given variant is in stock and return the new quantity + line."""
    line = wishlist.lines.filter(variant=variant).first()
    line_quantity = 0 if line is None else line.quantity

    new_quantity = quantity if replace else (quantity + line_quantity)

    if new_quantity < 0:
        raise ValueError(
            "%r is not a valid quantity (results in %r)" % (quantity, new_quantity)
        )

    if new_quantity > 0 and check_quantity:
        variant.check_quantity(new_quantity)

    return new_quantity, line


def add_variant_to_wishlist(
    wishlist, variant, quantity=1, replace=False, check_quantity=True
):
    """Add a product variant to wishlist.

    If `replace` is truthy then any previous quantity is discarded instead
    of added to.
    """

    new_quantity, line = check_variant_in_stock(
        wishlist,
        variant,
        quantity=quantity,
        replace=replace,
        check_quantity=check_quantity,
    )

    if line is None:
        line = wishlist.lines.filter(variant=variant).first()

    if new_quantity == 0:
        if line is not None:
            line.delete()
    elif line is None:
        wishlist.lines.create(wishlist=wishlist, variant=variant, quantity=new_quantity)
    elif new_quantity > 0:
        line.quantity = new_quantity
        line.save(update_fields=["quantity"])

    update_wishlist_quantity(wishlist)

def get_shipping_address_forms(wishlist, user_addresses, data, country):
    """Retrieve a form initialized with data based on the wishlist shipping address."""
    shipping_address = (
        wishlist.shipping_address or wishlist.user.default_shipping_address
    )

    if shipping_address and shipping_address in user_addresses:
        address_form, preview = get_address_form(
            data, country_code=country.code, initial={"country": country}
        )
        addresses_form = AddressChoiceForm(
            data, addresses=user_addresses, initial={"address": shipping_address.id}
        )
    elif shipping_address:
        address_form, preview = get_address_form(
            data, country_code=shipping_address.country.code, instance=shipping_address
        )
        addresses_form = AddressChoiceForm(data, addresses=user_addresses)
    else:
        address_form, preview = get_address_form(
            data, country_code=country.code, initial={"country": country}
        )
        addresses_form = AddressChoiceForm(data, addresses=user_addresses)

    return address_form, addresses_form, preview


def update_shipping_address_in_wishlist(wishlist, user_addresses, data, country):
    """Return shipping address choice forms and if an address was updated."""
    address_form, addresses_form, preview = get_shipping_address_forms(
        wishlist, user_addresses, data, country
    )

    updated = False

    if addresses_form.is_valid() and not preview:
        use_existing_address = (
            addresses_form.cleaned_data["address"] != AddressChoiceForm.NEW_ADDRESS
        )

        if use_existing_address:
            address_id = addresses_form.cleaned_data["address"]
            address = Address.objects.get(id=address_id)
            change_shipping_address_in_wishlist(wishlist, address)
            updated = True

        elif address_form.is_valid():
            address = address_form.save()
            change_shipping_address_in_wishlist(wishlist, address)
            updated = True

    return addresses_form, address_form, updated


def update_shipping_address_in_anonymous_wishlist(wishlist, data, country):
    """Return shipping address choice forms and if an address was updated."""
    address_form, preview = get_address_form(
        data,
        country_code=country.code,
        autocomplete_type="shipping",
        instance=wishlist.shipping_address,
        initial={"country": country},
    )
    user_form = AnonymousUserShippingForm(
        data if not preview else None, instance=wishlist
    )

    updated = False

    if user_form.is_valid() and address_form.is_valid():
        user_form.save()
        address = address_form.save()
        change_shipping_address_in_wishlist(wishlist, address)
        updated = True

    return user_form, address_form, updated


def get_billing_forms_with_shipping(wishlist, data, user_addresses, country):
    """Get billing form based on a the current billing and shipping data."""
    shipping_address = wishlist.shipping_address
    billing_address = wishlist.billing_address or Address(country=country)

    if not billing_address.id or billing_address == shipping_address:
        address_form, preview = get_address_form(
            data,
            country_code=shipping_address.country.code,
            autocomplete_type="billing",
            initial={"country": shipping_address.country},
        )
        addresses_form = BillingAddressChoiceForm(
            data,
            addresses=user_addresses,
            initial={"address": BillingAddressChoiceForm.SHIPPING_ADDRESS},
        )
    elif billing_address in user_addresses:
        address_form, preview = get_address_form(
            data,
            country_code=billing_address.country.code,
            autocomplete_type="billing",
            initial={"country": billing_address.country},
        )
        addresses_form = BillingAddressChoiceForm(
            data, addresses=user_addresses, initial={"address": billing_address.id}
        )
    else:
        address_form, preview = get_address_form(
            data,
            country_code=billing_address.country.code,
            autocomplete_type="billing",
            initial={"country": billing_address.country},
            instance=billing_address,
        )
        addresses_form = BillingAddressChoiceForm(
            data,
            addresses=user_addresses,
            initial={"address": BillingAddressChoiceForm.NEW_ADDRESS},
        )

    return address_form, addresses_form, preview


def update_billing_address_in_wishlist_with_shipping(
    wishlist, user_addresses, data, country
):
    """Return shipping address choice forms and if an address was updated."""
    address_form, addresses_form, preview = get_billing_forms_with_shipping(
        wishlist, data, user_addresses, country
    )

    updated = False

    if addresses_form.is_valid() and not preview:
        address = None
        address_id = addresses_form.cleaned_data["address"]

        if address_id == BillingAddressChoiceForm.SHIPPING_ADDRESS:
            if wishlist.user and wishlist.shipping_address in user_addresses:
                address = wishlist.shipping_address
            else:
                address = wishlist.shipping_address.get_copy()
        elif address_id != BillingAddressChoiceForm.NEW_ADDRESS:
            address = user_addresses.get(id=address_id)
        elif address_form.is_valid():
            address = address_form.save()

        if address:
            change_billing_address_in_wishlist(wishlist, address)
            updated = True

    return addresses_form, address_form, updated


def get_anonymous_summary_without_shipping_forms(wishlist, data, country):
    """Build a form initialized with data depending on addresses in wishlist."""
    billing_address = wishlist.billing_address

    if billing_address:
        address_form, preview = get_address_form(
            data,
            country_code=billing_address.country.code,
            autocomplete_type="billing",
            instance=billing_address,
        )
    else:
        address_form, preview = get_address_form(
            data,
            country_code=country.code,
            autocomplete_type="billing",
            initial={"country": country},
        )

    return address_form, preview


def update_billing_address_in_anonymous_wishlist(wishlist, data, country):
    """Return shipping address choice forms and if an address was updated."""
    address_form, preview = get_anonymous_summary_without_shipping_forms(
        wishlist, data, country
    )
    user_form = AnonymousUserBillingForm(data, instance=wishlist)

    updated = False

    if user_form.is_valid() and address_form.is_valid() and not preview:
        user_form.save()
        address = address_form.save()
        change_billing_address_in_wishlist(wishlist, address)
        updated = True

    return user_form, address_form, updated


def get_summary_without_shipping_forms(wishlist, user_addresses, data, country):
    """Build a forms initialized with data depending on addresses in wishlist."""
    billing_address = wishlist.billing_address

    if billing_address and billing_address in user_addresses:
        address_form, preview = get_address_form(
            data,
            autocomplete_type="billing",
            country_code=billing_address.country.code,
            initial={"country": billing_address.country},
        )
        initial_address = billing_address.id
    elif billing_address:
        address_form, preview = get_address_form(
            data,
            autocomplete_type="billing",
            country_code=billing_address.country.code,
            initial={"country": billing_address.country},
            instance=billing_address,
        )
        initial_address = AddressChoiceForm.NEW_ADDRESS
    else:
        address_form, preview = get_address_form(
            data,
            autocomplete_type="billing",
            country_code=country.code,
            initial={"country": country},
        )
        if wishlist.user and wishlist.user.default_billing_address:
            initial_address = wishlist.user.default_billing_address.id
        else:
            initial_address = AddressChoiceForm.NEW_ADDRESS

    addresses_form = AddressChoiceForm(
        data, addresses=user_addresses, initial={"address": initial_address}
    )
    return address_form, addresses_form, preview


def update_billing_address_in_wishlist(wishlist, user_addresses, data, country):
    """Return shipping address choice forms and if an address was updated."""
    address_form, addresses_form, preview = get_summary_without_shipping_forms(
        wishlist, user_addresses, data, country
    )

    updated = False

    if addresses_form.is_valid():
        use_existing_address = (
            addresses_form.cleaned_data["address"] != AddressChoiceForm.NEW_ADDRESS
        )

        if use_existing_address:
            address_id = addresses_form.cleaned_data["address"]
            address = Address.objects.get(id=address_id)
            change_billing_address_in_wishlist(wishlist, address)
            updated = True

        elif address_form.is_valid():
            address = address_form.save()
            change_billing_address_in_wishlist(wishlist, address)
            updated = True

    return addresses_form, address_form, updated


def _check_new_wishlist_address(wishlist, address, address_type):
    """Check if and address in wishlist has changed and if to remove old one."""
    if address_type == AddressType.BILLING:
        old_address = wishlist.billing_address
    else:
        old_address = wishlist.shipping_address

    has_address_changed = any(
        [
            not address and old_address,
            address and not old_address,
            address and old_address and address != old_address,
        ]
    )

    remove_old_address = (
        has_address_changed
        and old_address is not None
        and (not wishlist.user or old_address not in wishlist.user.addresses.all())
    )

    return has_address_changed, remove_old_address


def change_billing_address_in_wishlist(wishlist, address):
    """Save billing address in wishlist if changed.

    Remove previously saved address if not connected to any user.
    """
    changed, remove = _check_new_wishlist_address(
        wishlist, address, AddressType.BILLING
    )
    if changed:
        if remove:
            wishlist.billing_address.delete()
        wishlist.billing_address = address
        wishlist.save(update_fields=["billing_address"])


def change_shipping_address_in_wishlist(wishlist, address):
    """Save shipping address in wishlist if changed.

    Remove previously saved address if not connected to any user.
    """
    changed, remove = _check_new_wishlist_address(
        wishlist, address, AddressType.SHIPPING
    )
    if changed:
        if remove:
            wishlist.shipping_address.delete()
        wishlist.shipping_address = address
        wishlist.save(update_fields=["shipping_address"])


def get_wishlist_context(wishlist, discounts, currency=None, shipping_range=None):
    """Retrieve the data shared between views in wishlist process."""
    manager = get_extensions_manager()
    wishlist_total = (
        manager.calculate_wishlist_total(wishlist=wishlist, discounts=discounts)
        - wishlist.get_total_gift_cards_balance()
    )
    wishlist_total = max(wishlist_total, zero_taxed_money(wishlist_total.currency))
    wishlist_subtotal = manager.calculate_wishlist_subtotal(wishlist, discounts)
    shipping_price = manager.calculate_wishlist_shipping(wishlist, discounts)

    shipping_required = wishlist.is_shipping_required()
    total_with_shipping = TaxedMoneyRange(
        start=wishlist_subtotal, stop=wishlist_subtotal
    )
    if shipping_required and shipping_range:
        total_with_shipping = shipping_range + wishlist_subtotal
    context = {
        "wishlist": wishlist,
        "wishlist_are_taxes_handled": manager.taxes_are_enabled(),
        "wishlist_lines": [
            (line, manager.calculate_wishlist_line_total(line, discounts))
            for line in wishlist
        ],
        "wishlist_shipping_price": shipping_price,
        "wishlist_subtotal": wishlist_subtotal,
        "wishlist_total": wishlist_total,
        "shipping_required": wishlist.is_shipping_required(),
        "total_with_shipping": total_with_shipping,
    }

    if currency:
        context.update(
            local_wishlist_total=to_local_currency(wishlist_total, currency),
            local_wishlist_subtotal=to_local_currency(wishlist_subtotal, currency),
            local_total_with_shipping=to_local_currency(total_with_shipping, currency),
        )

    return context


def _get_shipping_voucher_discount_for_wishlist(voucher, wishlist, discounts=None):
    """Calculate discount value for a voucher of shipping type."""
    if not wishlist.is_shipping_required():
        msg = pgettext(
            "Voucher not applicable", "Your order does not require shipping."
        )
        raise NotApplicable(msg)
    shipping_method = wishlist.shipping_method
    if not shipping_method:
        msg = pgettext(
            "Voucher not applicable", "Please select a shipping method first."
        )
        raise NotApplicable(msg)

    # check if voucher is limited to specified countries
    shipping_country = wishlist.shipping_address.country
    if voucher.countries and shipping_country.code not in voucher.countries:
        msg = pgettext(
            "Voucher not applicable", "This offer is not valid in your country."
        )
        raise NotApplicable(msg)

    manager = get_extensions_manager()
    shipping_price = manager.calculate_wishlist_shipping(wishlist, discounts).gross
    return voucher.get_discount_amount_for(shipping_price)


def _get_products_voucher_discount(wishlist, voucher, discounts=None):
    """Calculate products discount value for a voucher, depending on its type."""
    prices = None
    if voucher.type == VoucherType.SPECIFIC_PRODUCT:
        prices = get_prices_of_discounted_specific_product(wishlist, voucher, discounts)
    if not prices:
        msg = pgettext(
            "Voucher not applicable", "This offer is only valid for selected items."
        )
        raise NotApplicable(msg)
    return get_products_voucher_discount(voucher, prices)


def get_voucher_discount_for_wishlist(voucher, wishlist, discounts=None) -> Money:
    """Calculate discount value depending on voucher and discount types.

    Raise NotApplicable if voucher of given type cannot be applied.
    """
    validate_voucher_for_wishlist(voucher, wishlist, discounts)
    if voucher.type == VoucherType.ENTIRE_ORDER:
        manager = get_extensions_manager()
        subtotal = manager.calculate_wishlist_subtotal(wishlist, discounts).gross
        return voucher.get_discount_amount_for(subtotal)
    if voucher.type == VoucherType.SHIPPING:
        return _get_shipping_voucher_discount_for_wishlist(voucher, wishlist, discounts)
    if voucher.type == VoucherType.SPECIFIC_PRODUCT:
        return _get_products_voucher_discount(wishlist, voucher, discounts)
    raise NotImplementedError("Unknown discount type")


def get_voucher_for_wishlist(wishlist, vouchers=None, with_lock=False):
    """Return voucher with voucher code saved in wishlist if active or None."""
    if wishlist.voucher_code is not None:
        if vouchers is None:
            vouchers = Voucher.objects.active(date=timezone.now())
        try:
            qs = vouchers
            if with_lock:
                qs = vouchers.select_for_update()
            return qs.get(code=wishlist.voucher_code)
        except Voucher.DoesNotExist:
            return None
    return None


def recalculate_wishlist_discount(wishlist, discounts):
    """Recalculate `wishlist.discount` based on the voucher.

    Will clear both voucher and discount if the discount is no longer
    applicable.
    """
    voucher = get_voucher_for_wishlist(wishlist)
    if voucher is not None:
        try:
            discount = get_voucher_discount_for_wishlist(voucher, wishlist, discounts)
        except NotApplicable:
            remove_voucher_from_wishlist(wishlist)
        else:
            manager = get_extensions_manager()
            subtotal = manager.calculate_wishlist_subtotal(wishlist, discounts).gross
            wishlist.discount = (
                min(discount, subtotal)
                if voucher.type != VoucherType.SHIPPING
                else discount
            )
            wishlist.discount_name = str(voucher)
            wishlist.translated_discount_name = (
                voucher.translated.name
                if voucher.translated.name != voucher.name
                else ""
            )
            wishlist.save(
                update_fields=[
                    "translated_discount_name",
                    "discount_amount",
                    "discount_name",
                    "currency",
                ]
            )
    else:
        remove_voucher_from_wishlist(wishlist)


def add_promo_code_to_wishlist(wishlist: Wishlist, promo_code: str, discounts=None):
    """Add gift card or voucher data to wishlist.

    Raise InvalidPromoCode if promo code does not match to any voucher or gift card.
    """
    if promo_code_is_voucher(promo_code):
        add_voucher_code_to_wishlist(wishlist, promo_code, discounts)
    elif promo_code_is_gift_card(promo_code):
        add_gift_card_code_to_wishlist(wishlist, promo_code)
    else:
        raise InvalidPromoCode()


def add_voucher_code_to_wishlist(wishlist: Wishlist, voucher_code: str, discounts=None):
    """Add voucher data to wishlist by code.

    Raise InvalidPromoCode() if voucher of given type cannot be applied.
    """
    try:
        voucher = Voucher.objects.active(date=timezone.now()).get(code=voucher_code)
    except Voucher.DoesNotExist:
        raise InvalidPromoCode()
    try:
        add_voucher_to_wishlist(wishlist, voucher, discounts)
    except NotApplicable:
        raise ValidationError(
            {
                "promo_code": ValidationError(
                    "Voucher is not applicable to that wishlist.",
                    code=WishlistErrorCode.VOUCHER_NOT_APPLICABLE,
                )
            }
        )


def add_voucher_to_wishlist(wishlist: Wishlist, voucher: Voucher, discounts=None):
    """Add voucher data to wishlist.

    Raise NotApplicable if voucher of given type cannot be applied.
    """
    discount = get_voucher_discount_for_wishlist(voucher, wishlist, discounts)
    wishlist.voucher_code = voucher.code
    wishlist.discount_name = voucher.name
    wishlist.translated_discount_name = (
        voucher.translated.name if voucher.translated.name != voucher.name else ""
    )
    wishlist.discount = discount
    wishlist.save(
        update_fields=[
            "voucher_code",
            "discount_name",
            "translated_discount_name",
            "discount_amount",
        ]
    )


def remove_promo_code_from_wishlist(wishlist: Wishlist, promo_code: str):
    """Remove gift card or voucher data from wishlist."""
    if promo_code_is_voucher(promo_code):
        remove_voucher_code_from_wishlist(wishlist, promo_code)
    elif promo_code_is_gift_card(promo_code):
        remove_gift_card_code_from_wishlist(wishlist, promo_code)


def remove_voucher_code_from_wishlist(wishlist: Wishlist, voucher_code: str):
    """Remove voucher data from wishlist by code."""
    existing_voucher = get_voucher_for_wishlist(wishlist)
    if existing_voucher and existing_voucher.code == voucher_code:
        remove_voucher_from_wishlist(wishlist)


def remove_voucher_from_wishlist(wishlist: Wishlist):
    """Remove voucher data from wishlist."""
    wishlist.voucher_code = None
    wishlist.discount_name = None
    wishlist.translated_discount_name = None
    wishlist.discount_amount = 0
    wishlist.save(
        update_fields=[
            "voucher_code",
            "discount_name",
            "translated_discount_name",
            "discount_amount",
            "currency",
        ]
    )


def get_valid_shipping_methods_for_wishlist(
    wishlist: Wishlist, discounts, country_code=None
):
    manager = get_extensions_manager()
    return ShippingMethod.objects.applicable_shipping_methods_for_instance(
        wishlist,
        price=manager.calculate_wishlist_subtotal(wishlist, discounts).gross,
        country_code=country_code,
    )


def is_valid_shipping_method(wishlist, discounts):
    """Check if shipping method is valid and remove (if not)."""
    if not wishlist.shipping_method:
        return False

    valid_methods = get_valid_shipping_methods_for_wishlist(wishlist, discounts)
    if valid_methods is None or wishlist.shipping_method not in valid_methods:
        clear_shipping_method(wishlist)
        return False
    return True


def get_shipping_price_estimate(wishlist: Wishlist, discounts, country_code):
    """Return the estimated price range for shipping for given order."""

    shipping_methods = get_valid_shipping_methods_for_wishlist(
        wishlist, discounts, country_code=country_code
    )

    if shipping_methods is None:
        return None

    min_price_amount, max_price_amount = shipping_methods.aggregate(
        price_amount_min=Min("price_amount"), price_amount_max=Max("price_amount")
    ).values()

    if min_price_amount is None:
        return None

    manager = get_extensions_manager()
    prices = MoneyRange(
        start=Money(min_price_amount, wishlist.currency),
        stop=Money(max_price_amount, wishlist.currency),
    )
    return manager.apply_taxes_to_shipping_price_range(prices, country_code)


def clear_shipping_method(wishlist):
    wishlist.shipping_method = None
    wishlist.save(update_fields=["shipping_method"])


def _get_voucher_data_for_order(wishlist):
    """Fetch, process and return voucher/discount data from wishlist.

    Careful! It should be called inside a transaction.

    :raises NotApplicable: When the voucher is not applicable in the current wishlist.
    """
    voucher = get_voucher_for_wishlist(wishlist, with_lock=True)

    if wishlist.voucher_code and not voucher:
        msg = pgettext(
            "Voucher not applicable",
            "Voucher expired in meantime. Order placement aborted.",
        )
        raise NotApplicable(msg)

    if not voucher:
        return {}

    increase_voucher_usage(voucher)
    if voucher.apply_once_per_customer:
        add_voucher_usage_by_customer(voucher, wishlist.get_customer_email())
    return {
        "voucher": voucher,
        "discount": wishlist.discount,
        "discount_name": wishlist.discount_name,
        "translated_discount_name": wishlist.translated_discount_name,
    }


def _process_shipping_data_for_order(wishlist, shipping_price):
    """Fetch, process and return shipping data from wishlist."""
    if not wishlist.is_shipping_required():
        return {}

    shipping_address = wishlist.shipping_address

    if wishlist.user:
        store_user_address(wishlist.user, shipping_address, AddressType.SHIPPING)
        if wishlist.user.addresses.filter(pk=shipping_address.pk).exists():
            shipping_address = shipping_address.get_copy()

    return {
        "shipping_address": shipping_address,
        "shipping_method": wishlist.shipping_method,
        "shipping_method_name": smart_text(wishlist.shipping_method),
        "shipping_price": shipping_price,
        "weight": wishlist.get_total_weight(),
    }


def _process_user_data_for_order(wishlist):
    """Fetch, process and return shipping data from wishlist."""
    billing_address = wishlist.billing_address

    if wishlist.user:
        store_user_address(wishlist.user, billing_address, AddressType.BILLING)
        if wishlist.user.addresses.filter(pk=billing_address.pk).exists():
            billing_address = billing_address.get_copy()

    return {
        "user": wishlist.user,
        "user_email": wishlist.get_customer_email(),
        "billing_address": billing_address,
        "customer_note": wishlist.note,
    }


def validate_gift_cards(wishlist: Wishlist):
    """Check if all gift cards assigned to wishlist are available."""
    if (
        not wishlist.gift_cards.count()
        == wishlist.gift_cards.active(date=date.today()).count()
    ):
        msg = pgettext(
            "Gift card not applicable",
            "Gift card has expired. Order placement cancelled.",
        )
        raise NotApplicable(msg)


def create_line_for_order(wishlist_line: "WishlistLine", discounts) -> OrderLine:
    """Create a line for the given order.

    :raises InsufficientStock: when there is not enough items in stock for this variant.
    """

    quantity = wishlist_line.quantity
    variant = wishlist_line.variant
    product = variant.product
    variant.check_quantity(quantity)

    product_name = str(product)
    variant_name = str(variant)

    translated_product_name = str(product.translated)
    translated_variant_name = str(variant.translated)

    if translated_product_name == product_name:
        translated_product_name = ""

    if translated_variant_name == variant_name:
        translated_variant_name = ""

    manager = get_extensions_manager()
    total_line_price = manager.calculate_wishlist_line_total(wishlist_line, discounts)
    unit_price = quantize_price(
        total_line_price / wishlist_line.quantity, total_line_price.currency
    )
    line = OrderLine(
        product_name=product_name,
        variant_name=variant_name,
        translated_product_name=translated_product_name,
        translated_variant_name=translated_variant_name,
        product_sku=variant.sku,
        is_shipping_required=variant.is_shipping_required(),
        quantity=quantity,
        variant=variant,
        unit_price=unit_price,
        tax_rate=unit_price.tax / unit_price.net,
    )

    return line


def prepare_order_data(*, wishlist: Wishlist, tracking_code: str, discounts) -> dict:
    """Run checks and return all the data from a given wishlist to create an order.

    :raises NotApplicable InsufficientStock:
    """
    order_data = {}

    manager = get_extensions_manager()
    total = (
        manager.calculate_wishlist_total(wishlist=wishlist, discounts=discounts)
        - wishlist.get_total_gift_cards_balance()
    )
    total = max(total, zero_taxed_money(total.currency))

    shipping_total = manager.calculate_wishlist_shipping(wishlist, discounts)
    order_data.update(_process_shipping_data_for_order(wishlist, shipping_total))
    order_data.update(_process_user_data_for_order(wishlist))
    order_data.update(
        {
            "language_code": get_language(),
            "tracking_client_id": tracking_code,
            "total": total,
        }
    )

    order_data["lines"] = [
        create_line_for_order(wishlist_line=line, discounts=discounts)
        for line in wishlist
    ]

    # validate wishlist gift cards
    validate_gift_cards(wishlist)

    # Get voucher data (last) as they require a transaction
    order_data.update(_get_voucher_data_for_order(wishlist))

    # assign gift cards to the order
    order_data["total_price_left"] = (
        manager.calculate_wishlist_subtotal(wishlist, discounts)
        + shipping_total
        - wishlist.discount
    ).gross

    manager.preprocess_order_creation(wishlist, discounts)
    return order_data


def abort_order_data(order_data: dict):
    if "voucher" in order_data:
        voucher = order_data["voucher"]
        decrease_voucher_usage(voucher)
        if "user_email" in order_data:
            remove_voucher_usage_by_customer(voucher, order_data["user_email"])


@transaction.atomic
def create_order(*, wishlist: Wishlist, order_data: dict, user: User) -> Order:
    """Create an order from the wishlist.

    Each order will get a private copy of both the billing and the shipping
    address (if shipping).

    If any of the addresses is new and the user is logged in the address
    will also get saved to that user's address book.

    Current user's language is saved in the order so we can later determine
    which language to use when sending email.
    """
    from ..product.utils import allocate_stock
    from ..order.utils import add_gift_card_to_order

    order = Order.objects.filter(wishlist_token=wishlist.token).first()
    if order is not None:
        return order

    total_price_left = order_data.pop("total_price_left")
    order_lines = order_data.pop("lines")

    order = Order.objects.create(**order_data, wishlist_token=wishlist.token)
    order.lines.set(order_lines, bulk=False)

    # allocate stocks from the lines
    for line in order_lines:  # type: OrderLine
        variant = line.variant
        if variant.track_inventory:
            allocate_stock(variant, line.quantity)

    # Add gift cards to the order
    for gift_card in wishlist.gift_cards.select_for_update():
        total_price_left = add_gift_card_to_order(order, gift_card, total_price_left)

    # assign wishlist payments to the order
    wishlist.payments.update(order=order)

    order_created(order=order, user=user)

    # Send the order confirmation email
    send_order_confirmation.delay(order.pk, user.pk)
    return order


def is_fully_paid(wishlist: Wishlist, discounts):
    """Check if provided payment methods cover the wishlist's total amount.

    Note that these payments may not be captured or charged at all.
    """
    payments = [payment for payment in wishlist.payments.all() if payment.is_active]
    total_paid = sum([p.total for p in payments])
    manager = get_extensions_manager()
    wishlist_total = (
        manager.calculate_wishlist_total(wishlist=wishlist, discounts=discounts)
        - wishlist.get_total_gift_cards_balance()
    )
    wishlist_total = max(
        wishlist_total, zero_taxed_money(wishlist_total.currency)
    ).gross
    return total_paid >= wishlist_total.amount


def clean_wishlist(wishlist: Wishlist, discounts):
    """Check if wishlist can be completed."""
    if wishlist.is_shipping_required():
        if not wishlist.shipping_method:
            raise ValidationError(
                "Shipping method is not set",
                code=WishlistErrorCode.SHIPPING_METHOD_NOT_SET,
            )
        if not wishlist.shipping_address:
            raise ValidationError(
                "Shipping address is not set",
                code=WishlistErrorCode.SHIPPING_ADDRESS_NOT_SET,
            )
        if not is_valid_shipping_method(wishlist, discounts):
            raise ValidationError(
                "Shipping method is not valid for your shipping address",
                code=WishlistErrorCode.INVALID_SHIPPING_METHOD,
            )

    if not wishlist.billing_address:
        raise ValidationError(
            "Billing address is not set", code=WishlistErrorCode.BILLING_ADDRESS_NOT_SET
        )

    if not is_fully_paid(wishlist, discounts):
        raise ValidationError(
            "Provided payment methods can not cover the wishlist's total amount",
            code=WishlistErrorCode.WISHLIST_NOT_FULLY_PAID,
        )
