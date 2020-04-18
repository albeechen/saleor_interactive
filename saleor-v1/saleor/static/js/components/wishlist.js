import { getAjaxError } from "./misc";

// Wishlist quantity form
  let $checkoutLine = $(".checkout-preview-wishlist__line");
  $checkoutLine.each(function() {
    let $quantityInput = $(this).find("#id_quantity");
    let wishlistFormUrl = $(this)
      .find(".form-wishlist")
      .attr("action");
    let $deleteIcon = $(this).find(".checkout-preview-wishlist-item-delete");
    $(this).on("change", $quantityInput, e => {
      let newQuantity = $quantityInput.val();
      $.ajax({
        url: wishlistFormUrl,
        method: "POST",
        data: { quantity: newQuantity },
        success: response => {
          if (newQuantity === 0) {
            if (response.wishlist.numLines === 0) {
              location.reload();
            } else {
              $(this).fadeOut();
            }
          } 
        },
      });
    });
    $deleteIcon.on("click", e => {
      $.ajax({
        url: wishlistFormUrl,
        method: "POST",
        data: { quantity: 0 },
        success: response => {
          if (response.wishlist.numLines >= 1) {
            $(this).fadeOut();
          } else {
            location.reload();
          }
        }
      });
    });
  });


//Clear all wishlist

 $(".checkout-preview-wishlist__clear").click(e => {
    $.ajax({
      url: $(".checkout-preview-wishlist__clear").data("action"),
      method: "POST",
      data: {},
      success: response => {
        location.reload();
      }
    });
  }); 