(function(){
"use strict";

function ready(fn){
    if(document.readyState==="loading"){
        document.addEventListener("DOMContentLoaded",fn);
    }else{
        fn();
    }
}

function number(raw){
    var value=parseFloat(String(raw||"0").replace(/,/g,""));
    return Number.isFinite(value)?value:0;
}

function sample(points){
    if(!window.matchMedia("(max-width: 600px)").matches){
        return points;
    }
    if(points.length<=7){return points}
    return points.slice(-7);
}

function svgEl(name,attrs){
    var el=document.createElementNS("http://www.w3.org/2000/svg",name);
    Object.keys(attrs||{}).forEach(function(key){
        el.setAttribute(key,String(attrs[key]));
    });
    return el;
}

function compact(value){
    var abs=Math.abs(value);
    if(abs>=1000000){return (value/1000000).toFixed(abs>=10000000?0:1)+"M"}
    if(abs>=1000){return (value/1000).toFixed(abs>=10000?0:1)+"K"}
    return String(Math.round(value));
}

function drawTrend(host){
    var dataHost=host.parentElement.querySelector(".f3-chart-data");
    if(!dataHost){return}

    var points=Array.from(dataHost.children).map(function(el){
        return {
            label:el.dataset.label||"",
            one:number(el.dataset.value),
            two:number(el.dataset.value2)
        };
    });

    points=sample(points);

    if(!points.length){
        host.innerHTML='<div class="f3-chart-empty">Hakuna data ya chart bado.</div>';
        return;
    }

    var width=720,height=250,left=48,right=15,top=18,bottom=42;
    var plotW=width-left-right,plotH=height-top-bottom;
    var maxValue=Math.max.apply(null,points.map(function(p){return Math.max(p.one,p.two)}));
    if(maxValue<=0){maxValue=1}

    var svg=svgEl("svg",{
        viewBox:"0 0 "+width+" "+height,
        role:"img",
        "aria-label":host.getAttribute("aria-label")||"Business trend chart"
    });

    for(var g=0;g<=4;g++){
        var y=top+(plotH/4)*g;
        svg.appendChild(svgEl("line",{
            x1:left,y1:y,x2:width-right,y2:y,
            stroke:"#cbd5e1","stroke-width":"1","stroke-dasharray":"3 5",opacity:".55"
        }));
        var value=maxValue*(1-g/4);
        var label=svgEl("text",{
            x:left-8,y:y+4,"text-anchor":"end",
            fill:"#64748b","font-size":"10"
        });
        label.textContent=compact(value);
        svg.appendChild(label);
    }

    function xy(index,value){
        var x=points.length===1
            ? left+plotW/2
            : left+(plotW*index/(points.length-1));
        var y=top+plotH-(value/maxValue)*plotH;
        return [x,y];
    }

    function drawSeries(key,color){
        var coords=points.map(function(p,i){return xy(i,p[key])});
        var poly=svgEl("polyline",{
            points:coords.map(function(p){return p[0]+","+p[1]}).join(" "),
            fill:"none",stroke:color,"stroke-width":"3",
            "stroke-linecap":"round","stroke-linejoin":"round"
        });
        svg.appendChild(poly);

        coords.forEach(function(p){
            svg.appendChild(svgEl("circle",{
                cx:p[0],cy:p[1],r:"3.2",fill:color
            }));
        });
    }

    drawSeries("one","#2563eb");
    drawSeries("two","#dc2626");

    points.forEach(function(p,i){
        if(points.length>8 && i%2!==0 && i!==points.length-1){return}
        var pos=xy(i,0);
        var label=svgEl("text",{
            x:pos[0],y:height-17,"text-anchor":"middle",
            fill:"#64748b","font-size":"9"
        });
        label.textContent=p.label;
        svg.appendChild(label);
    });

    host.innerHTML="";
    host.appendChild(svg);

    var legend=document.createElement("div");
    legend.className="f3-chart-legend";
    var one=host.dataset.seriesOne||"Series 1";
    var two=host.dataset.seriesTwo||"Series 2";
    legend.innerHTML=
        '<span><i style="background:#2563eb"></i>'+one+'</span>'+
        '<span><i style="background:#dc2626"></i>'+two+'</span>';
    host.appendChild(legend);
}

function enhanceTables(){
    document.querySelectorAll(
        ".daily-report .report-table,"+
        ".monthly-report .report-table,"+
        ".profit-report .sales-table,"+
        ".f3-profit-list table"
    ).forEach(function(table){
        if(table.closest(".summary-table")){return}
        if(table.classList.contains("f3-responsive-table")){return}

        var headers=Array.from(table.querySelectorAll("thead th"))
            .map(function(th){return th.textContent.trim()});

        if(!headers.length){return}

        table.classList.add("f3-responsive-table");

        table.querySelectorAll("tbody tr").forEach(function(row){
            Array.from(row.children).forEach(function(cell,index){
                cell.dataset.label=headers[index]||"";
            });
        });
    });
}

function addPrintButtons(){
    var report=document.querySelector(
        ".daily-report,.monthly-report,.profit-report,.f3-profit-list"
    );
    if(!report){return}

    var actions=report.querySelector(".report-actions,.top-actions");
    if(!actions||actions.querySelector("[data-f3-print]")){return}

    var button=document.createElement("button");
    button.type="button";
    button.className="button f3-print-button no-print";
    button.dataset.f3Print="1";
    button.textContent="Print / PDF";
    button.addEventListener("click",function(){
        window.print();
    });
    actions.appendChild(button);
}

ready(function(){
    document.querySelectorAll('[data-f3-chart="trend"]').forEach(drawTrend);
    enhanceTables();
    addPrintButtons();
});
})();
