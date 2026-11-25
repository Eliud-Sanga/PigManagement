(function(){
"use strict";
function ready(fn){if(document.readyState==="loading"){document.addEventListener("DOMContentLoaded",fn)}else{fn()}}
ready(function(){
document.querySelectorAll("[data-password-toggle]").forEach(function(button){
var input=document.getElementById(button.dataset.passwordToggle);if(!input){return}
button.addEventListener("click",function(){var showing=input.type==="text";input.type=showing?"password":"text";button.textContent=showing?"Show":"Hide";button.setAttribute("aria-label",showing?"Show password":"Hide password")});
});
var otp=document.querySelector("[data-otp-input]");
if(otp){otp.addEventListener("input",function(){otp.value=otp.value.replace(/\D/g,"").slice(0,6)});otp.addEventListener("paste",function(event){var text=(event.clipboardData||window.clipboardData).getData("text").replace(/\D/g,"").slice(0,6);if(text){event.preventDefault();otp.value=text}})}
var p=document.getElementById("new_password1"),c=document.getElementById("new_password2"),m=document.querySelector("[data-password-match]");
function match(){if(!p||!c||!m){return}if(!c.value){m.textContent="";return}if(p.value===c.value){m.textContent="Passwords match.";m.style.color="var(--success)"}else{m.textContent="Passwords do not match.";m.style.color="var(--danger)"}}
if(p&&c){p.addEventListener("input",match);c.addEventListener("input",match)}
});
})();
