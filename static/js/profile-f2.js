(function(){
"use strict";
function ready(fn){if(document.readyState==="loading"){document.addEventListener("DOMContentLoaded",fn)}else{fn()}}
ready(function(){
document.querySelectorAll("[data-f2-confirm-form]").forEach(function(form){
var source=form.querySelector("[data-f2-password-confirm]");
var overlay=form.parentElement.querySelector("[data-f2-confirm-overlay]");
if(!source||!overlay){return}
source.classList.add("f2-password-source");source.required=false;
var popup=overlay.querySelector("[data-f2-popup-password]");
var cancel=overlay.querySelector("[data-f2-confirm-cancel]");
var confirm=overlay.querySelector("[data-f2-confirm-save]");
var confirmed=false;
function open(){overlay.classList.add("show");overlay.setAttribute("aria-hidden","false");document.body.style.overflow="hidden";popup.value="";setTimeout(function(){popup.focus()},20)}
function close(){overlay.classList.remove("show");overlay.setAttribute("aria-hidden","true");document.body.style.overflow=""}
form.addEventListener("submit",function(e){if(confirmed){confirmed=false;return}e.preventDefault();open()});
cancel.addEventListener("click",close);
confirm.addEventListener("click",function(){if(!popup.value){popup.focus();return}source.value=popup.value;confirmed=true;close();if(form.requestSubmit){form.requestSubmit()}else{form.submit()}});
overlay.addEventListener("click",function(e){if(e.target===overlay){close()}});
document.addEventListener("keydown",function(e){if(e.key==="Escape"&&overlay.classList.contains("show")){close()}});
});
});
})();
