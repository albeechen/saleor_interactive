
function changeImg(imgList){
	if($(imgList[0]).hasClass("active")){
		$(imgList[0]).removeClass("active");
		$(imgList[1]).addClass("active");
	}else{
		$(imgList[1]).removeClass("active");
		$(imgList[0]).addClass("active");
	}

}

export default $(document).ready(
	

	e => {
			
		let pl = document.getElementsByClassName("product-list");

		for (var i = 0; i < pl.length; i++) {	
		  	pl[i].addEventListener("mouseover", function() {
		  		let imgListIn = this.getElementsByTagName("img");
				changeImg(imgListIn);
			});

			pl[i].addEventListener("mouseout", function() {
				let imgListOut = this.getElementsByTagName("img");
				changeImg(imgListOut);
			});
		};
	}
);