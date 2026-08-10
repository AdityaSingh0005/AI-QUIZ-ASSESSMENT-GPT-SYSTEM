let time = 600;


let timer = setInterval(function(){


let minutes = Math.floor(time / 60);

let seconds = time % 60;


seconds = seconds < 10 ? "0" + seconds : seconds;


document.getElementById("time").innerHTML =
minutes + ":" + seconds;



time--;



if(time < 0){


clearInterval(timer);


alert("Time Over! Quiz Submitted");


document.querySelector("form").submit();


}


},1000);