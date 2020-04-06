from functools import wraps

from django.core.exceptions import ValidationError
from django.shortcuts import redirect

from ..utils import is_valid_shipping_method


def validate_wishlist(view):
    """Decorate a view making it require a non-empty wishlist.

    If the wishlist is empty, redirect to the wishlist details.
    """

    @wraps(view)
    def func(request, wishlist):
        if wishlist:
            return view(request, wishlist)
        return redirect("wishlist:index")

    return func


def validate_shipping_address(view):
    """Decorate a view making it require a valid shipping address.

    If either the shipping address or customer email is empty, redirect to the
    shipping address step.

    Expects to be decorated with `@validate_wishlist`.
    """

    @wraps(view)
    def func(request, wishlist):
        if not wishlist.email or not wishlist.shipping_address:
            return redirect("wishlist:shipping-address")
        try:
            wishlist.shipping_address.full_clean()
        except ValidationError:
            return redirect("wishlist:shipping-address")
        return view(request, wishlist)

    return func


def validate_shipping_method(view):
    """Decorate a view making it require a shipping method.

    If the method is missing or incorrect, redirect to the shipping method
    step.

    Expects to be decorated with `@validate_wishlist`.
    """

    @wraps(view)
    def func(request, wishlist):
        if not is_valid_shipping_method(wishlist, request.discounts):
            return redirect("wishlist:shipping-method")
        return view(request, wishlist)

    return func


def validate_is_shipping_required(view):
    """Decorate a view making it check if wishlist needs shipping.

    If shipping is not needed, redirect to the wishlist summary.

    Expects to be decorated with `@validate_wishlist`.
    """

    @wraps(view)
    def func(request, wishlist):
        if not wishlist.is_shipping_required():
            return redirect("wishlist:summary")
        return view(request, wishlist)

    return func
