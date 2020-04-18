
let slideIndex = 0;
let lineNum    = 4;
let $product_list = document.getElementsByClassName("product-list");
let $load_btn = $("#load-more-item"); 

function plusDivs() {

  	slideIndex += lineNum;
  	if (slideIndex >= $product_list.length) {
  		slideIndex = $product_list.length;
  		$load_btn.text("Show less");
  	}
  	
  	for (let i = 0; i < slideIndex; i++)
    	$product_list[i].style.display = "block";  
  	
}

function subtract() {
	
  	if ((slideIndex-lineNum) <= 0){
  		slideIndex = 0;
  		$load_btn.text("Show more");
  	}else if(((slideIndex)%lineNum) != 0){
  		slideIndex = slideIndex - (slideIndex % lineNum);
  	}else
  		slideIndex = slideIndex - lineNum;

  	for (let i = ($product_list.length-1); i >= slideIndex ; i--) 
   		$product_list[i].style.display = "none"; 
  	
}


$load_btn.click(e => {

		if($load_btn.text() == "Show more"){	
			plusDivs();
		}else{
			subtract();
		}
})

export default $(document).ready(e => { plusDivs(); })
