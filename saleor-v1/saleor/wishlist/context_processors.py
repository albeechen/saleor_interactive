"""Wishlist-related context processors."""
from .utils import get_wishlist_from_request


def wishlist_counter(request):
    """Expose the number of items in wishlist."""
    wishlist = get_wishlist_from_request(request)
    return {"wishlist_counter": wishlist.quantity}
