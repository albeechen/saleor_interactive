from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.utils.translation import pgettext

from ...account.models import Address
from ...core import analytics
from ...core.exceptions import InsufficientStock
from ...core.taxes import TaxError
from ...discount.models import NotApplicable
from ..forms import WishlistNoteForm
from ..utils import (
    create_order,
    get_wishlist_context,
    prepare_order_data,
    update_billing_address_in_anonymous_wishlist,
    update_billing_address_in_wishlist,
    update_billing_address_in_wishlist_with_shipping,
)


@transaction.atomic()
def _handle_order_placement(request, wishlist):
    """Try to create an order and redirect the user as necessary.

    This function creates an order from wishlist and performs post-create actions
    such as removing the wishlist instance, sending order notification email
    and creating order history events.
    """
    try:
        # Run checks an prepare the data for order creation
        order_data = prepare_order_data(
            wishlist=wishlist,
            tracking_code=analytics.get_client_id(request),
            discounts=request.discounts,
        )
    except InsufficientStock:
        return redirect("wishlist:index")
    except NotApplicable:
        messages.warning(
            request, pgettext("Wishlist warning", "Please review your wishlist.")
        )
        return redirect("wishlist:summary")
    except TaxError as tax_error:
        messages.warning(
            request,
            pgettext(
                "Wishlist warning", "Unable to calculate taxes - %s" % str(tax_error)
            ),
        )
        return redirect("wishlist:summary")

    # Push the order data into the database
    order = create_order(wishlist=wishlist, order_data=order_data, user=request.user)

    # remove wishlist after order is created
    wishlist.delete()

    # Redirect the user to the payment page
    return redirect("order:payment", token=order.token)


def summary_with_shipping_view(request, wishlist):
    """Display order summary with billing forms for a logged in user.

    Will create an order if all data is valid.
    """
    note_form = WshlistNoteForm(request.POST or None, instance=wishlist)
    if note_form.is_valid():
        note_form.save()

    user_addresses = (
        wishlist.user.addresses.all() if wishlist.user else Address.objects.none()
    )

    addresses_form, address_form, updated = update_billing_address_in_wishlist_with_shipping(  # noqa
        wishlist, user_addresses, request.POST or None, request.country
    )

    if updated:
        return _handle_order_placement(request, wishlist)

    ctx = get_wishlist_context(wishlist, request.discounts)
    ctx.update(
        {
            "additional_addresses": user_addresses,
            "address_form": address_form,
            "addresses_form": addresses_form,
            "note_form": note_form,
        }
    )
    return TemplateResponse(request, "wishlist/summary.html", ctx)


def anonymous_summary_without_shipping(request, wishlist):
    """Display order summary with billing forms for an unauthorized user.

    Will create an order if all data is valid.
    """
    note_form = WishlistNoteForm(request.POST or None, instance=wishlist)
    if note_form.is_valid():
        note_form.save()

    user_form, address_form, updated = update_billing_address_in_anonymous_wishlist(
        wishlist, request.POST or None, request.country
    )

    if updated:
        return _handle_order_placement(request, wishlist)

    ctx = get_wishlist_context(wishlist, request.discounts)
    ctx.update(
        {"address_form": address_form, "note_form": note_form, "user_form": user_form}
    )
    return TemplateResponse(request, "wishlist/summary_without_shipping.html", ctx)


def summary_without_shipping(request, wishlist):
    """Display order summary for cases where shipping is not required.

    Will create an order if all data is valid.
    """
    note_form = WishlistNoteForm(request.POST or None, instance=wishlist)
    if note_form.is_valid():
        note_form.save()

    user_addresses = wishlist.user.addresses.all()

    addresses_form, address_form, updated = update_billing_address_in_wishlist(
        wishlist, user_addresses, request.POST or None, request.country
    )

    if updated:
        return _handle_order_placement(request, wishlist)

    ctx = get_wishlist_context(wishlist, request.discounts)
    ctx.update(
        {
            "additional_addresses": user_addresses,
            "address_form": address_form,
            "addresses_form": addresses_form,
            "note_form": note_form,
        }
    )
    return TemplateResponse(request, "wishlist/summary_without_shipping.html", ctx)
