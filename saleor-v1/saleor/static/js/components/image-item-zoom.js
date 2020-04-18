
function imageZoom(imgID, resultID) {
  var img, lens, result, cx, cy;
  img = document.getElementById(imgID);
  result = document.getElementById(resultID);
  
  lens = document.createElement("DIV");
  lens.setAttribute("class", "img-zoom-lens");
  lens.setAttribute("style", "height:50%; width:50%; border:1px solid grey; position: absolute; order: 1px solid #d4d4d4");
  $(".img-zoom-lens").hide();
  
  img.parentElement.insertBefore(lens, img);
  
  cx = result.offsetWidth / lens.offsetWidth;
  cy = result.offsetHeight / lens.offsetHeight;
  
  result.style.backgroundImage = "url('" + img.src + "')";
  result.style.backgroundSize = (img.width * cx) + "px " + (img.height * cy) + "px";
 
  lens.addEventListener("mousemove", moveLens);
  img.addEventListener("mousemove", moveLens);
 
  lens.addEventListener("touchmove", moveLens);
  img.addEventListener("touchmove", moveLens);

  function moveLens(e) {
    var pos, x, y;
   
    e.preventDefault();
    
    pos = getCursorPos(e);
   
    x = pos.x - (lens.offsetWidth / 2);
    y = pos.y - (lens.offsetHeight / 2);
    
    if (x > img.width - lens.offsetWidth) {x = img.width - lens.offsetWidth;}
    if (x < 0) {x = 0;}
    if (y > img.height - lens.offsetHeight) {y = img.height - lens.offsetHeight;}
    if (y < 0) {y = 0;}
   
    lens.style.left = x + "px";
    lens.style.top = y + "px";
   
    result.style.backgroundPosition = "-" + (x * cx) + "px -" + (y * cy) + "px";
  }

  function getCursorPos(e) {
    var a, x = 0, y = 0;
    e = e || window.event;
    
    a = img.getBoundingClientRect();
    
    x = e.pageX - a.left;
    y = e.pageY - a.top;
   
    x = x - window.pageXOffset;
    y = y - window.pageYOffset;
    return {x : x, y : y};
  }
}

function imageZoomShow() {
	//clear data
	imageZoomHide();
	

	if( document.querySelector('div.carousel-inner div.active div img') != null ){
		let	imgId = document.querySelector('div.carousel-inner div.active div img').id;
		let result = document.getElementById("product__zoom__result");
		result.setAttribute("style", "height:540px; width:540px; border: 1px solid #d4d4d4; position: absolute; z-index: 1000; background-color: white");
	  
	  imageZoom(imgId, "product__zoom__result");
	}
	$("#product__zoom__result").show();
}

function imageZoomHide() {
	$("#product__zoom__result").hide();
   	$(".img-zoom-lens").remove();
}

export default $(document).ready(
e => {
	$(".carousel-indicators").click(
		e => {
			imageZoomShow();
		},
		e => {
			imageZoomHide();
		}
	);

  $(".product-image").hover(
    e => {
    	imageZoomShow();
  	},
    e => {
    	imageZoomHide();
  	}
  );
});