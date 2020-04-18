from django.conf.urls import url

from . import views
from .views.discount import remove_voucher_view

urlpatterns = [
    url(r"^$", views.wishlist_index, name="index"),
    url(r"^start$", views.wishlist_start, name="start"),
    url(
        r"^update/(?P<variant_id>\d+)/$", views.update_wishlist_line, name="update-line"
    ),
    url(r"^clear/$", views.clear_wishlist, name="clear"),
    url(
        r"^shipping-options/$", views.wishlist_shipping_options, name="shipping-options"
    ),
    url(
        r"^shipping-address/", views.wishlist_shipping_address, name="shipping-address"
    ),
    url(r"^shipping-method/", views.wishlist_shipping_method, name="shipping-method"),
    url(r"^summary/", views.wishlist_order_summary, name="summary"),
    url(r"^dropdown/$", views.wishlist_dropdown, name="dropdown"),
    url(r"^remove_voucher/", remove_voucher_view, name="remove-voucher"),
    url(r"^login/", views.wishlist_login, name="login"),
]
