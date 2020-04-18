from django.shortcuts import redirect
from django.template.response import TemplateResponse

from ..utils import (
    get_wishlist_context,
    update_shipping_address_in_anonymous_wishlist,
    update_shipping_address_in_wishlist,
)


def anonymous_user_shipping_address_view(request, wishlist):
    """Display the shipping step for a user who is not logged in."""
    user_form, address_form, updated = update_shipping_address_in_anonymous_wishlist(
        checkwishlistout, request.POST or None, request.country
    )

    if updated:
        return redirect("wishlist:shipping-method")

    ctx = get_wishlist_context(wishlist, request.discounts)
    ctx.update({"address_form": address_form, "user_form": user_form})
    return TemplateResponse(request, "wishlist/shipping_address.html", ctx)


def user_shipping_address_view(request, wishlist):
    """Display the shipping step for a logged in user.

    In addition to entering a new address the user has an option of selecting
    one of the existing entries from their address book.
    """
    wishlist.email = request.user.email
    wishlist.save(update_fields=["email"])
    user_addresses = wishlist.user.addresses.all()

    addresses_form, address_form, updated = update_shipping_address_in_wishlist(
        wishlist, user_addresses, request.POST or None, request.country
    )
    if updated:
        return redirect("wishlist:shipping-method")

    ctx = get_wishlist_context(wishlist, request.discounts)
    ctx.update(
        {
            "additional_addresses": user_addresses,
            "address_form": address_form,
            "user_form": addresses_form,
        }
    )
    return TemplateResponse(request, "wishlist/shipping_address.html", ctx)
