"""Wishlist-related forms and fields."""
from typing import Any

from django import forms
from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS
from django.utils import timezone
from django.utils.encoding import smart_text
from django.utils.safestring import mark_safe
from django.utils.translation import npgettext_lazy, pgettext_lazy
from django_countries.fields import Country, LazyTypedChoiceField

from ..core.exceptions import InsufficientStock
from ..core.taxes import display_gross_prices
from ..core.utils import format_money
from ..discount.models import NotApplicable, Voucher
from ..extensions.manager import get_extensions_manager
from ..shipping.models import ShippingMethod, ShippingZone
from .models import Wishlist


class QuantityField(forms.IntegerField):
    """A specialized integer field with initial quantity and min/max values."""

    def __init__(self, **kwargs):
        super().__init__(
            min_value=0,
            max_value=settings.MAX_CHECKOUT_LINE_QUANTITY,
            initial=1,
            **kwargs,
        )


class AddToWishlistForm(forms.Form):
    """Add-to-wishlist form.

    Allows selection of a product variant and quantity.

    The save method adds it to the wishlist.
    """

    quantity = QuantityField(
        label=pgettext_lazy("Add to wishlist form field label", "Quantity")
    )
    error_messages = {
        "not-available": pgettext_lazy(
            "Add to wishlist form error",
            "Sorry. This product is currently not available.",
        ),
        "empty-stock": pgettext_lazy(
            "Add to wishlist form error",
            "Sorry. This product is currently out of stock.",
        ),
        "variant-too-many-in-wishlist": pgettext_lazy(
            "Add to wishlist form error",
            "Sorry. You can't add more than %d times this item.",
        ),
        "variant-does-not-exists": pgettext_lazy(
            "Add to wishlist form error", "Oops. We could not find that product."
        ),
        "insufficient-stock": npgettext_lazy(
            "Add to wishlist form error",
            "Only %d remaining in stock.",
            "Only %d remaining in stock.",
        ),
    }

    def __init__(self, *args, **kwargs):
        self.wishlist = kwargs.pop("wishlist")
        self.product = kwargs.pop("product")
        self.discounts = kwargs.pop("discounts", ())
        self.country = kwargs.pop("country", {})
        self.extensions = kwargs.pop("extensions", None)
        super().__init__(*args, **kwargs)

    def add_error_i18n(self, field, error_name, fmt: Any = tuple()):
        self.add_error(field, self.error_messages[error_name] % fmt)

    def clean(self):
        """Clean the form.

        Makes sure the total quantity in wishlist (taking into account what was
        already there) does not exceed available quantity.
        """
        cleaned_data = super().clean()
        quantity = cleaned_data.get("quantity")
        if quantity is None:
            return cleaned_data
        variant = self.get_variant(cleaned_data)
        if variant is None:
            self.add_error_i18n(NON_FIELD_ERRORS, "variant-does-not-exists")
        else:
            line = self.wishlist.get_line(variant)
            used_quantity = line.quantity if line else 0
            new_quantity = quantity + used_quantity

            if new_quantity > settings.MAX_CHECKOUT_LINE_QUANTITY:
                self.add_error_i18n(
                    "quantity",
                    "variant-too-many-in-wishlist",
                    settings.MAX_CHECKOUT_LINE_QUANTITY,
                )
                return cleaned_data

            try:
                variant.check_quantity(new_quantity)
            except InsufficientStock as e:
                remaining = e.item.quantity_available - used_quantity
                if remaining:
                    self.add_error_i18n("quantity", "insufficient-stock", remaining)
                else:
                    self.add_error_i18n("quantity", "empty-stock")
        return cleaned_data

    def save(self):
        """Add the selected product variant and quantity to the wishlist."""
        from .utils import add_variant_to_wishlist

        variant = self.get_variant(self.cleaned_data)
        quantity = self.cleaned_data["quantity"]
        add_variant_to_wishlist(self.wishlist, variant, quantity)

    def get_variant(self, cleaned_data):
        """Return a product variant that matches submitted values.

        This allows specialized implementations to select the variant based on
        multiple fields (like size and color) instead of having a single
        variant selection widget.
        """
        raise NotImplementedError()


class ReplaceWishlistLineForm(AddToWishlistForm):
    """Replace quantity in wishlist form.

    Similar to AddTowishlistForm but its save method replaces the quantity.
    """

    def __init__(self, *args, **kwargs):
        self.variant = kwargs.pop("variant")
        super().__init__(*args, product=self.variant.product, **kwargs)
        self.fields["quantity"].widget.attrs = {
            "min": 1,
            "max": settings.MAX_CHECKOUT_LINE_QUANTITY,
        }

    def clean_quantity(self):
        """Clean the quantity field.

        Checks if target quantity does not exceed the currently available
        quantity.
        """
        quantity = self.cleaned_data["quantity"]
        try:
            self.variant.check_quantity(quantity)
        except InsufficientStock as e:
            msg = self.error_messages["insufficient-stock"]
            raise forms.ValidationError(msg % e.item.quantity_available, code=e.code)
        return quantity

    def clean(self):
        """Clean the form skipping the add-to-form checks."""
        # explicitly skip parent's implementation
        # pylint: disable=E1003
        return super(AddToWishlistForm, self).clean()

    def get_variant(self, cleaned_data):
        """Return the matching variant.

        In this case we explicitly know the variant as we're modifying an
        existing line in wishlist.
        """
        return self.variant

    def save(self):
        """Replace the selected product's quantity in wishlist."""
        from .utils import add_variant_to_wishlist

        variant = self.get_variant(self.cleaned_data)
        quantity = self.cleaned_data["quantity"]
        add_variant_to_wishlist(self.wishlist, variant, quantity, replace=True)


class CountryForm(forms.Form):
    """Country selection form."""

    country = LazyTypedChoiceField(
        label=pgettext_lazy("Country form field label", "Country"), choices=[]
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        available_countries = {
            (country.code, country.name)
            for shipping_zone in ShippingZone.objects.all()
            for country in shipping_zone.countries
        }
        self.fields["country"].choices = sorted(
            available_countries, key=lambda choice: choice[1]
        )

    def get_shipping_price_estimate(self, wishlist, discounts):
        """Return a shipping price range for given order for the selected country."""
        from .utils import get_shipping_price_estimate

        country = self.cleaned_data["country"]
        if isinstance(country, str):
            country = Country(country)
        return get_shipping_price_estimate(wishlist, discounts, country)


class AnonymousUserShippingForm(forms.ModelForm):
    """Additional shipping information form for users who are not logged in."""

    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"autocomplete": "shipping email"}),
        label=pgettext_lazy("Address form field label", "Email"),
    )

    class Meta:
        model = Wishlist
        fields = ["email"]


class AnonymousUserBillingForm(forms.ModelForm):
    """Additional billing information form for users who are not logged in."""

    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"autocomplete": "billing email"}),
        label=pgettext_lazy("Address form field label", "Email"),
    )

    class Meta:
        model = Wishlist
        fields = ["email"]


class AddressChoiceForm(forms.Form):
    """Choose one of user's addresses or to create new one."""

    NEW_ADDRESS = "new_address"
    CHOICES = [
        (
            NEW_ADDRESS,
            pgettext_lazy("Shipping addresses form choice", "Enter a new address"),
        )
    ]

    address = forms.ChoiceField(
        label=pgettext_lazy("Shipping addresses form field label", "Address"),
        choices=CHOICES,
        initial=NEW_ADDRESS,
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, **kwargs):
        addresses = kwargs.pop("addresses")
        super().__init__(*args, **kwargs)
        address_choices = [(address.id, str(address)) for address in addresses]
        self.fields["address"].choices = self.CHOICES + address_choices


class BillingAddressChoiceForm(AddressChoiceForm):
    """Choose one of user's addresses, a shipping one or to create new."""

    NEW_ADDRESS = "new_address"
    SHIPPING_ADDRESS = "shipping_address"
    CHOICES = [
        (
            NEW_ADDRESS,
            pgettext_lazy("Billing addresses form choice", "Enter a new address"),
        ),
        (
            SHIPPING_ADDRESS,
            pgettext_lazy("Billing addresses form choice", "Same as shipping"),
        ),
    ]

    address = forms.ChoiceField(
        label=pgettext_lazy("Billing addresses form field label", "Address"),
        choices=CHOICES,
        initial=SHIPPING_ADDRESS,
        widget=forms.RadioSelect,
    )


class ShippingMethodChoiceField(forms.ModelChoiceField):
    """Shipping method choice field.

    Uses a radio group instead of a dropdown and includes estimated shipping
    prices.
    """

    shipping_address = None
    extensions = None
    widget = forms.RadioSelect()

    def label_from_instance(self, obj):
        """Return a friendly label for the shipping method."""
        if display_gross_prices():
            price = self.extensions.apply_taxes_to_shipping(
                obj.price, shipping_address=self.shipping_address
            ).gross
        else:
            price = obj.price
        price_html = format_money(price)
        label = mark_safe("%s %s" % (obj.name, price_html))
        return label


class WishlistShippingMethodForm(forms.ModelForm):
    shipping_method = ShippingMethodChoiceField(
        queryset=ShippingMethod.objects.all(),
        label=pgettext_lazy("Shipping method form field label", "Shipping method"),
        empty_label=None,
    )

    class Meta:
        model = Wishlist
        fields = ["shipping_method"]

    def __init__(self, *args, **kwargs):
        from .utils import get_valid_shipping_methods_for_wishlist

        discounts = kwargs.pop("discounts")
        extensions = get_extensions_manager()
        super().__init__(*args, **kwargs)
        shipping_address = self.instance.shipping_address
        country_code = shipping_address.country.code
        qs = get_valid_shipping_methods_for_wishlist(
            self.instance, discounts, country_code=country_code
        )
        self.fields["shipping_method"].queryset = qs
        self.fields["shipping_method"].shipping_address = shipping_address
        self.fields["shipping_method"].extensions = extensions

        if self.initial.get("shipping_method") is None:
            shipping_methods = qs.all()
            if shipping_methods:
                self.initial["shipping_method"] = shipping_methods[0]


class WishlistNoteForm(forms.ModelForm):
    """Save note in wishlist."""

    note = forms.CharField(
        max_length=250,
        required=False,
        strip=True,
        label=False,
        widget=forms.Textarea({"rows": 3}),
    )

    class Meta:
        model = Wishlist
        fields = ["note"]


class VoucherField(forms.ModelChoiceField):

    default_error_messages = {
        "invalid_choice": pgettext_lazy(
            "Voucher form error", "Discount code incorrect or expired"
        )
    }


class WishlistVoucherForm(forms.ModelForm):
    """Apply voucher to a wishlist form."""

    voucher = VoucherField(
        queryset=Voucher.objects.none(),
        to_field_name="code",
        help_text=pgettext_lazy(
            "Wishlist discount form label for voucher field",
            "Gift card or discount code",
        ),
        widget=forms.TextInput,
    )

    class Meta:
        model = Wishlist
        fields = ["voucher"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["voucher"].queryset = Voucher.objects.active(date=timezone.now())

    def clean(self):
        from .utils import get_voucher_discount_for_wishlist

        cleaned_data = super().clean()
        if "voucher" in cleaned_data:
            voucher = cleaned_data["voucher"]
            try:
                discount = get_voucher_discount_for_wishlist(voucher, self.instance)
                cleaned_data["discount"] = discount
            except NotApplicable as e:
                self.add_error("voucher", smart_text(e))
        return cleaned_data

    def save(self, commit=True):
        voucher = self.cleaned_data["voucher"]
        self.instance.voucher_code = voucher.code
        self.instance.discount_name = voucher.name
        self.instance.translated_discount_name = (
            voucher.translated.name if voucher.translated.name != voucher.name else ""
        )
        self.instance.discount = self.cleaned_data["discount"]
        return super().save(commit)
