from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.translation import pgettext
from django.views.decorators.http import require_POST

from ...discount.models import Voucher
from ..forms import WishlistVoucherForm
from ..models import Wishlist
from ..utils import (
    get_or_empty_db_wishlist,
    recalculate_wishlist_discount,
    remove_voucher_from_wishlist,
)


def add_voucher_form(view):
    """Decorate a view injecting a voucher form and handling its submission."""

    @wraps(view)
    def func(request, wishlist):
        prefix = "discount"
        data = {k: v for k, v in request.POST.items() if k.startswith(prefix)}
        voucher_form = WishlistVoucherForm(
            data or None, prefix=prefix, instance=wishlist
        )
        if voucher_form.is_bound:
            if voucher_form.is_valid():
                voucher_form.save()
                next_url = request.GET.get("next", request.META["HTTP_REFERER"])
                return redirect(next_url)
            else:
                remove_voucher_from_wishlist(wishlist)
                # if only discount form was used we clear post for other forms
                request.POST = {}
        else:
            recalculate_wishlist_discount(wishlist, request.discounts)
        response = view(request, wishlist)
        if isinstance(response, TemplateResponse):
            response.context_data["voucher_form"] = voucher_form
        return response

    return func


def validate_voucher(view):
    """Decorate a view making it check whether a discount voucher is valid.

    If the voucher is invalid it will be removed and the user will be
    redirected to the wishlist summary view.
    """

    @wraps(view)
    def func(request, wishlist):
        if wishlist.voucher_code:
            try:
                Voucher.objects.active(date=timezone.now()).get(
                    code=wishlist.voucher_code
                )
            except Voucher.DoesNotExist:
                remove_voucher_from_wishlist(wishlist)
                msg = pgettext(
                    "Wishlist warning",
                    "This voucher has expired. Please review your wishlist.",
                )
                messages.warning(request, msg)
                return redirect("wishlist:summary")
        return view(request, wishlist)

    return func


@require_POST
@get_or_empty_db_wishlist(Wishlist.objects.for_display())
def remove_voucher_view(request, wishlist):
    """Clear the discount and remove the voucher."""
    next_url = request.GET.get("next", request.META["HTTP_REFERER"])
    remove_voucher_from_wishlist(wishlist)
    return redirect(next_url)
