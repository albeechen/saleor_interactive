"""Wishlist related views."""
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse

from ...account.forms import LoginForm
from ...core.taxes import get_display_price, quantize_price, zero_taxed_money
from ...core.utils import format_money, get_user_shipping_country, to_local_currency
from ..forms import WishlistShippingMethodForm, CountryForm, ReplaceWishlistLineForm
from ..models import Wishlist
from ..utils import (
    check_product_availability_and_warn,
    get_wishlist_context,
    get_or_empty_db_wishlist,
    get_shipping_price_estimate,
    is_valid_shipping_method,
    update_wishlist_quantity,
)
from .discount import add_voucher_form, validate_voucher
from .shipping import anonymous_user_shipping_address_view, user_shipping_address_view
from .summary import (
    anonymous_summary_without_shipping,
    summary_with_shipping_view,
    summary_without_shipping,
)
from .validators import (
    validate_wishlist,
    validate_is_shipping_required,
    validate_shipping_address,
    validate_shipping_method,
)


@get_or_empty_db_wishlist(Wishlist.objects.for_display())
@validate_wishlist
def wishlist_login(request, wishlist):
    """Allow the user to log in prior to wishlist."""
    if request.user.is_authenticated:
        return redirect("wishlist:start")
    ctx = {"form": LoginForm()}
    return TemplateResponse(request, "wishlist/login.html", ctx)


@get_or_empty_db_wishlist(Wishlist.objects.for_display())
@validate_wishlist
@validate_is_shipping_required
def wishlist_start(request, wishlist):
    """Redirect to the initial step of wishlist."""
    return redirect("wishlist:shipping-address")


@get_or_empty_db_wishlist(Wishlist.objects.for_display())
@validate_voucher
@validate_wishlist
@validate_is_shipping_required
@add_voucher_form
def wishlist_shipping_address(request, wishlist):
    """Display the correct shipping address step."""
    if request.user.is_authenticated:
        return user_shipping_address_view(request, wishlist)
    return anonymous_user_shipping_address_view(request, wishlist)


@get_or_empty_db_wishlist(Wishlist.objects.for_display())
@validate_voucher
@validate_wishlist
@validate_is_shipping_required
@validate_shipping_address
@add_voucher_form
def wishlist_shipping_method(request, wishlist):
    """Display the shipping method selection step."""
    discounts = request.discounts
    is_valid_shipping_method(wishlist, discounts)

    form = WishlistShippingMethodForm(
        request.POST or None,
        discounts=discounts,
        instance=wishlist,
        initial={"shipping_method": wishlist.shipping_method},
    )
    if form.is_valid():
        form.save()
        return redirect("wishlist:summary")

    ctx = get_wishlist_context(wishlist, discounts)
    ctx.update({"shipping_method_form": form})
    return TemplateResponse(request, "wishlist/shipping_method.html", ctx)


@get_or_empty_db_wishlist(Wishlist.objects.for_display())
@validate_voucher
@validate_wishlist
@add_voucher_form
def wishlist_order_summary(request, wishlist):
    """Display the correct order summary."""
    if wishlist.is_shipping_required():
        view = validate_shipping_method(summary_with_shipping_view)
        view = validate_shipping_address(view)
        return view(request, wishlist)
    if request.user.is_authenticated:
        return summary_without_shipping(request, wishlist)
    return anonymous_summary_without_shipping(request, wishlist)


@get_or_empty_db_wishlist(wishlist_queryset=Wishlist.objects.for_display())
def wishlist_index(request, wishlist):
    """Display wishlist details."""
    discounts = request.discounts
    wishlist_lines = []
    check_product_availability_and_warn(request, wishlist)

    # refresh required to get updated wishlist lines and it's quantity
    try:
        wishlist = Wishlist.objects.prefetch_related(
            "lines__variant__product__category"
        ).get(pk=wishlist.pk)
    except Wishlist.DoesNotExist:
        pass

    lines = wishlist.lines.select_related("variant__product__product_type")
    lines = lines.prefetch_related(
        "variant__translations",
        "variant__product__translations",
        "variant__product__images",
        "variant__product__product_type__variant_attributes__translations",
        "variant__images",
        "variant__product__product_type__variant_attributes",
    )
    manager = request.extensions
    for line in lines:
        initial = {"quantity": line.quantity}
        form = ReplaceWishlistLineForm(
            None,
            wishlist=wishlist,
            variant=line.variant,
            initial=initial,
            discounts=discounts,
        )
        total_line = manager.calculate_wishlist_line_total(line, discounts)
        variant_price = quantize_price(total_line / line.quantity, total_line.currency)
        wishlist_lines.append(
            {
                "variant": line.variant,
                "get_price": variant_price,
                "get_total": total_line,
                "form": form,
            }
        )

    default_country = get_user_shipping_country(request)
    country_form = CountryForm(initial={"country": default_country})
    shipping_price_range = get_shipping_price_estimate(
        wishlist, discounts, country_code=default_country
    )

    context = get_wishlist_context(
        wishlist,
        discounts,
        currency=request.currency,
        shipping_range=shipping_price_range,
    )
    context.update(
        {
            "wishlist_lines": wishlist_lines,
            "country_form": country_form,
            "shipping_price_range": shipping_price_range,
        }
    )
    return TemplateResponse(request, "wishlist/index.html", context)


@get_or_empty_db_wishlist(wishlist_queryset=Wishlist.objects.for_display())
def wishlist_shipping_options(request, wishlist):
    """Display shipping options to get a price estimate."""
    country_form = CountryForm(request.POST or None)
    if country_form.is_valid():
        shipping_price_range = country_form.get_shipping_price_estimate(
            wishlist, request.discounts
        )
    else:
        shipping_price_range = None
    ctx = {"shipping_price_range": shipping_price_range, "country_form": country_form}
    wishlist_data = get_wishlist_context(
        wishlist,
        request.discounts,
        currency=request.currency,
        shipping_range=shipping_price_range,
    )
    ctx.update(wishlist_data)
    return TemplateResponse(request, "wishlist/_subtotal_table.html", ctx)


@get_or_empty_db_wishlist(Wishlist.objects.prefetch_related("lines__variant__product"))
def update_wishlist_line(request, wishlist, variant_id):
    """Update the line quantities."""
    if not request.is_ajax():
        return redirect("wishlist:index")

    wishlist_line = get_object_or_404(wishlist.lines, variant_id=variant_id)
    discounts = request.discounts
    status = None
    form = ReplaceWishlistLineForm(
        request.POST,
        wishlist=wishlist,
        variant=wishlist_line.variant,
        discounts=discounts,
    )
    manager = request.extensions
    if form.is_valid():
        form.save()
        wishlist.refresh_from_db()
        # Refresh obj from db and confirm that wishlist still has this line
        wishlist_line = wishlist.lines.filter(variant_id=variant_id).first()
        line_total = zero_taxed_money(currency=settings.DEFAULT_CURRENCY)
        if wishlist_line:
            line_total = manager.calculate_wishlist_line_total(wishlist_line, discounts)
        subtotal = get_display_price(line_total)
        response = {
            "variantId": variant_id,
            "subtotal": format_money(subtotal),
            "total": 0,
            "wishlist": {"numItems": wishlist.quantity, "numLines": len(wishlist)},
        }

        wishlist_total = manager.calculate_wishlist_subtotal(wishlist, discounts)
        wishlist_total = get_display_price(wishlist_total)
        response["total"] = format_money(wishlist_total)
        local_wishlist_total = to_local_currency(wishlist_total, request.currency)
        if local_wishlist_total is not None:
            response["localTotal"] = format_money(local_wishlist_total)
        
        status = 200

    elif request.POST is not None:
        response = {"error": form.errors}
        status = 400
    return JsonResponse(response, status=status)


@get_or_empty_db_wishlist()
def clear_wishlist(request, wishlist):
    """Clear wishlist."""
    if not request.is_ajax():
        return redirect("wishlist:index")
    wishlist.lines.all().delete()
    update_wishlist_quantity(wishlist)
    response = {"numItems": 0}
    return JsonResponse(response)


@get_or_empty_db_wishlist(wishlist_queryset=Wishlist.objects.for_display())
def wishlist_dropdown(request, wishlist):
    """Display a wishlist summary suitable for displaying on all pages."""
    discounts = request.discounts
    manager = request.extensions

    def prepare_line_data(line):
        first_image = line.variant.get_first_image()
        if first_image:
            first_image = first_image.image
        return {
            "product": line.variant.product,
            "variant": line.variant,
            "quantity": line.quantity,
            "image": first_image,
            "line_total": manager.calculate_wishlist_line_total(line, discounts),
            "variant_url": line.variant.get_absolute_url(),
        }

    if wishlist.quantity == 0:
        data = {"quantity": 0}
    else:
        data = {
            "quantity": wishlist.quantity,
            "total": manager.calculate_wishlist_subtotal(wishlist, discounts),
            "lines": [prepare_line_data(line) for line in wishlist],
        }

    data = "saved!"

    return render(request, "wishlist_dropdown.html", data)
