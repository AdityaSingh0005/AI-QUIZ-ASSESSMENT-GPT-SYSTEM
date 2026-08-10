const btn = document.getElementById("darkModeBtn");


if(btn){

    btn.onclick = function(){

        document.body.classList.toggle("dark");


        if(document.body.classList.contains("dark")){

            localStorage.setItem("theme","dark");

        }
        else{

            localStorage.setItem("theme","light");

        }

    };

}



if(localStorage.getItem("theme") === "dark"){

    document.body.classList.add("dark");

}


if(localStorage.getItem("theme") === "dark"){

    document.body.classList.add("dark");

}