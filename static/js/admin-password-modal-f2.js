(function(){
"use strict";
function ready(fn){if(document.readyState==="loading"){document.addEventListener("DOMContentLoaded",fn)}else{fn()}}
ready(function(){
var pendingForm=null,pendingSource=null;
var overlay=document.createElement("div");
overlay.className="f2-admin-confirm-overlay";
overlay.setAttribute("aria-hidden","true");
overlay.innerHTML='<div class="f2-admin-confirm-dialog" role="dialog" aria-modal="true"><div class="f2-admin-confirm-icon">✓</div><h3>Confirm administrator action</h3><p>Enter your current Platform Administrator password to continue.</p><input type="password" autocomplete="current-password" placeholder="Administrator password" data-admin-popup-password><div class="f2-admin-confirm-actions"><button type="button" class="f2-admin-cancel">Cancel</button><button type="button" class="f2-admin-confirm">Confirm & continue</button></div></div>';
document.body.appendChild(overlay);
var popup=overlay.querySelector("[data-admin-popup-password]");
function open(form,source){pendingForm=form;pendingSource=source;popup.value="";overlay.classList.add("show");overlay.setAttribute("aria-hidden","false");document.body.style.overflow="hidden";setTimeout(function(){popup.focus()},20)}
function close(){overlay.classList.remove("show");overlay.setAttribute("aria-hidden","true");document.body.style.overflow="";pendingForm=null;pendingSource=null}
document.querySelectorAll('form input[name="admin_password"]').forEach(function(source){
var form=source.form;if(!form){return}
source.required=false;source.classList.add("f2-admin-password-source");
var field=source.closest(".field");if(field){field.classList.add("f2-admin-password-field")}
form.addEventListener("submit",function(e){if(form.dataset.f2AdminConfirmed==="1"){delete form.dataset.f2AdminConfirmed;return}e.preventDefault();open(form,source)});
});
overlay.querySelector(".f2-admin-cancel").addEventListener("click",close);
overlay.querySelector(".f2-admin-confirm").addEventListener("click",function(){
if(!pendingForm||!pendingSource){close();return}
if(!popup.value){popup.focus();return}
var form=pendingForm,source=pendingSource;
source.value=popup.value;form.dataset.f2AdminConfirmed="1";
overlay.classList.remove("show");overlay.setAttribute("aria-hidden","true");document.body.style.overflow="";
if(form.requestSubmit){form.requestSubmit()}else{form.submit()}
pendingForm=null;pendingSource=null;
});
overlay.addEventListener("click",function(e){if(e.target===overlay){close()}});
document.addEventListener("keydown",function(e){if(e.key==="Escape"&&overlay.classList.contains("show")){close()}});
});
})();
