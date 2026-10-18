#!/usr/bin/env python3
"""chart_lab - a tiny, isolated harness for the chart control.

One page, one chart, one control panel. No dashboard, no models, no state files.
It exists so the chart component can be iterated and tested on its own.

    python3 chart_lab.py --port 8800   ->  http://127.0.0.1:8800
"""

import argparse
import json
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BACKEND = "http://127.0.0.1:8787"

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>chart lab</title>
<style>
 :root{--bg:#080b10;--panel:#0f1721;--line:#1d2836;--fg:#e8eef6;--dim:#8494a8;
       --faint:#59677a;--up:#3fb950;--down:#f85149;--mono:ui-monospace,Menlo,monospace}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:13px/1.5 -apple-system,BlinkMacSystemFont,var(--mono)}
 header{display:flex;align-items:center;gap:12px;padding:12px 18px;background:#0b1119;
        border-bottom:1px solid var(--line);flex-wrap:wrap}
 h1{margin:0;font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:var(--dim)}
 .sym{font-family:var(--mono);font-size:18px;font-weight:700;color:#58a6ff}
 .px{font-family:var(--mono);font-size:18px}
 .chg{font-family:var(--mono);font-size:13px}
 .up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--dim)}
 .spacer{flex:1}
 input,button{font:inherit}
 .symin{background:#0f1721;border:1px solid #24313e;border-radius:8px;color:var(--fg);
        padding:7px 10px;width:132px;font-family:var(--mono);font-size:12px}
 .symin:focus{outline:none;border-color:#3d5470}
 button.ghost{background:#131c26;border:1px solid #2a3a4a;color:var(--dim);
              border-radius:8px;padding:7px 13px;font-size:12px;cursor:pointer}
 button.ghost:hover{color:var(--fg);border-color:#3d5470}
 .wrap{max-width:1320px;margin:0 auto;padding:14px 18px 22px}
 .chartcard{background:linear-gradient(180deg,#0f1721,#0b1219);border:1px solid var(--line);
            border-radius:14px;padding:12px 14px 8px;position:relative}
 svg{width:100%;height:470px;display:block;touch-action:none;cursor:crosshair;user-select:none}
 .pop{position:absolute;right:14px;top:52px;z-index:40;width:min(620px,94vw);display:none;
      background:linear-gradient(180deg,#101923,#0b1219);border:1px solid #22303d;
      border-radius:14px;box-shadow:0 30px 70px -30px #000}
 .pop.open{display:block}
 .phead{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid #1b2836}
 .phead b{font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--dim)}
 .pbody{padding:10px 16px;max-height:60vh;overflow:auto}
 .prow{display:grid;grid-template-columns:92px 1fr;gap:12px;padding:9px 0;
       border-bottom:1px dashed #17222e;align-items:start}
 .prow:last-child{border-bottom:none}
 .plab{font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);
       padding-top:7px}
 .chips{display:flex;flex-wrap:wrap;gap:6px}
 .chip{display:inline-flex;align-items:center;gap:6px;padding:5px 11px;border-radius:8px;
       border:1px solid #1e2733;background:linear-gradient(180deg,#111a23,#0e161e);
       color:var(--dim);font-size:11px;cursor:pointer;transition:.15s}
 .chip:hover{color:var(--fg);border-color:#35506a}
 .chip.on{color:#dbe9f7;border-color:#3d6f96;background:linear-gradient(180deg,#182a3b,#132231);
          box-shadow:0 0 0 1px #58a6ff22}
 .chip.on:before{content:"";width:5px;height:5px;border-radius:50%;background:#58a6ff}
 .chip.armed{background:linear-gradient(180deg,#f0b429,#d29922);border-color:#f0b429;
             color:#0d1117;font-weight:600}
 .pfoot{padding:9px 16px 12px;border-top:1px solid #1b2836;font-size:10.5px;color:#4c5a6c;
        line-height:1.7}
 .pfoot b{color:#7d8b9e}
 .legend{display:flex;gap:14px;flex-wrap:wrap;padding:6px 2px 2px;font-size:11px;color:var(--dim)}
 .dot{width:9px;height:9px;border-radius:3px;display:inline-block}
 .hint{font-size:10.5px;color:#4c5a6c;padding:4px 2px 0}
</style></head><body>

<header>
  <h1>chart lab</h1>
  <input class="symin" id="sym" value="BTCUSDT"
         onkeydown="if(event.key==='Enter'){load(this.value.trim().toUpperCase());}">
  <span class="sym" id="title">BTCUSDT</span>
  <span class="px" id="px">—</span>
  <span class="chg" id="chg">—</span>
  <span class="spacer"></span>
  <span class="hint" id="range">—</span>
  <button class="ghost" id="ctlbtn" onclick="toggleCtl()">⚙ control</button>
</header>

<div class="wrap">
  <div class="chartcard">
    <div class="pop" id="ctl">
      <div class="phead"><b>control</b><span class="hint" id="ctlsub"></span>
        <span class="spacer"></span>
        <button class="ghost" onclick="resetAll()">reset</button></div>
      <div class="pbody">
        <div class="prow"><span class="plab">timeframe</span>
          <div class="chips" id="ivs"></div></div>
        <div class="prow"><span class="plab">layers</span>
          <div class="chips" id="layers"></div></div>
        <div class="prow"><span class="plab">view</span>
          <div class="chips" id="view"></div></div>
      </div>
      <div class="pfoot"><b>mouse</b> — drag box: zoom time+price · shift-drag: pan ·
        wheel: zoom time · shift+wheel: zoom price · double-click: reset<br>
        <b>trackpad</b> — two-finger: pan · pinch: zoom</div>
    </div>
    <div id="chartbar" style="position:relative">
      <div style="position:absolute;left:8px;top:6px;z-index:5;display:flex;gap:6px;align-items:center">
        <button class="chip" onclick="zoomX((PL+W-PR)/2,(S.c||DATA.length)*0.65)" title="zoom in (+)">+</button>
        <button class="chip" onclick="zoomX((PL+W-PR)/2,(S.c||DATA.length)*1.55)" title="zoom out (−)">−</button>
        <button class="chip" onclick="resetAll()" title="reset (0)">reset</button>
        <button class="chip" onclick="zoomY(H/2,1)" title="reset price axis (y)">price</button>
        <span class="hint" style="margin-left:8px">drag a box to zoom · shift-drag to pan · wheel zooms time · shift+wheel zooms price</span>
      </div>
      <svg id="svg" viewBox="0 0 1200 470" preserveAspectRatio="none"
           style="width:100%;height:470px;display:block;touch-action:none;cursor:crosshair"></svg>
    </div>
    <div class="legend" id="legend"></div>
    <div class="hint">drag a box to zoom both axes · measure tool draws Δ% / bars / time</div>
  </div>
</div>

<script>
var W=1200, H=470, PL=64, PR=78, PT=12, PB=92;
var S={sym:"BTCUSDT", iv:"5m", s:0, c:null, y:null, measure:false, off:false};
var DATA=[], IND={}, MEAS=null, BAND=null, G={pts:new Map(), mode:null, x0:0, y0:0, s0:0, c0:0, pinch:0};
var OV={vol:true, ema:true, vwap:true, session:false, atr:false, rsi:true};
var LAYERS=[
 {id:"vol", label:"volume"},
 {id:"ema", label:"EMA 20/50"},
 {id:"vwap", label:"VWAP"},
 {id:"session", label:"session H/L", extent:function(d){
    return [Math.min.apply(null,d.l), Math.max.apply(null,d.h)];}},
 {id:"atr", label:"ATR envelope", extent:function(d){
    var a=(d.atr||[]).filter(function(v){return v!=null;});
    if(!a.length) return null;
    var m=Math.max.apply(null,a);
    return [Math.min.apply(null,d.l)-m, Math.max.apply(null,d.h)+m];}},
 {id:"rsi", label:"RSI"}
];

function $(id){ return document.getElementById(id); }
function esc(s){ return String(s==null?"":s).replace(/[&<>]/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;"}[c]; }); }
function money(v){ return Number(v||0).toLocaleString(undefined,{maximumFractionDigits:0}); }

function load(sym){
  if(sym) S.sym=sym;
  $("sym").value=S.sym;
  var url="/api/klines?symbol="+encodeURIComponent(S.sym)+"&interval="+S.iv+"&limit=300";
  fetch(url,{cache:"no-store"}).then(function(r){ return r.json(); }).then(function(d){
    DATA=(d.rows||[]).map(function(b){ return {t:b.t,o:b.o,h:b.h,l:b.l,c:b.c,v:b.v}; });
    IND=d.indicators||{};
    if(!DATA.length){ $("px").textContent="no data"; return; }
    S.c=null; S.s=0; S.y=null; MEAS=null;
    render();
  }).catch(function(e){ $("px").textContent="load failed"; });
}

function lay(id){ return OV[id]!==false; }

function render(){
  if(!DATA.length) return;
  var n=DATA.length;
  var vc=Math.max(20,Math.min(n,S.c||n));
  var vs=Math.max(0,Math.min(n-vc,S.s||0));
  S.c=vc; S.s=vs;
  var win=DATA.slice(vs,vs+vc);

  var hw=win.map(function(b){return b.h;}), lw=win.map(function(b){return b.l;});
  var wlo=Math.min.apply(null,lw), whi=Math.max.apply(null,hw);
  var LO,HI;
  if(S.y){ LO=S.y.lo; HI=S.y.hi; }
  else{
    var pad=(whi-wlo)*0.07||1;
    LO=wlo-pad; HI=whi+pad;
    LAYERS.forEach(function(L){
      if(!lay(L.id)||!L.extent) return;
      var e=L.extent({l:lw,h:hw,atr:IND.atr20});
      if(e&&isFinite(e[0])&&isFinite(e[1])){
        if(e[0]<LO) LO=e[0]-pad*0.2;
        if(e[1]>HI) HI=e[1]+pad*0.2;
      }
    });
  }
  var y=function(v){ return PT+(H-PT-PB)*(1-(v-LO)/(HI-LO)); };
  var x=function(i){ return PL+(W-PL-PR)*((i-vs)/Math.max(1,vc-1)); };
  var bw=Math.max(1.2,(W-PL-PR)/vc*0.62);
  var maxV=Math.max.apply(null,win.map(function(b){return b.v||0;}))||1;
  var yv=function(v){ return H-16-((H-PT-PB)*0.18)*((v||0)/maxV); };

  var svg='<defs><linearGradient id="upg" x1="0" y1="0" x2="0" y2="1">'+
    '<stop offset="0%" stop-color="#3fb950" stop-opacity=".85"/>'+
    '<stop offset="100%" stop-color="#2ea043" stop-opacity=".3"/></linearGradient>'+
    '<linearGradient id="dng" x1="0" y1="0" x2="0" y2="1">'+
    '<stop offset="0%" stop-color="#f85149" stop-opacity=".85"/>'+
    '<stop offset="100%" stop-color="#da3633" stop-opacity=".3"/></linearGradient></defs>';

  for(var g=0;g<=4;g++){
    var gv=LO+(HI-LO)*g/4;
    svg+='<line x1="'+PL+'" y1="'+y(gv).toFixed(1)+'" x2="'+(W-PR)+'" y2="'+y(gv).toFixed(1)+
         '" stroke="#1a2431"/>';
    svg+='<text x="'+(PL-8)+'" y="'+(y(gv)+3.5).toFixed(1)+'" fill="#4c5a6c" font-size="10.5"'+
         ' text-anchor="end" font-family="ui-monospace,monospace">'+gv.toPrecision(7)+'</text>';
    svg+='<text x="'+(W-PR+8)+'" y="'+(y(gv)+3.5).toFixed(1)+'" fill="#3f4c5c" font-size="9.5"'+
         ' font-family="ui-monospace,monospace">'+gv.toPrecision(7)+'</text>';
  }

  if(OV.vol){
    win.forEach(function(b,k){
      var i=vs+k, up=b.c>=b.o;
      svg+='<rect x="'+(x(i)-bw/2).toFixed(1)+'" y="'+yv(b.v).toFixed(1)+'" width="'+bw.toFixed(1)+
           '" height="'+Math.max(0.5,H-16-yv(b.v)).toFixed(1)+'" fill="'+(up?"#3fb950":"#f85149")+
           '" opacity=".22"/>';
    });
  }

  win.forEach(function(b,k){
    var i=vs+k, up=b.c>=b.o, col=up?"#3fb950":"#f85149";
    svg+='<line x1="'+x(i).toFixed(1)+'" y1="'+y(b.h).toFixed(1)+'" x2="'+x(i).toFixed(1)+
         '" y2="'+y(b.l).toFixed(1)+'" stroke="'+col+'" opacity=".85"/>';
    var y1=y(Math.max(b.o,b.c)), y2=y(Math.min(b.o,b.c));
    svg+='<rect x="'+(x(i)-bw/2).toFixed(1)+'" y="'+y1.toFixed(1)+'" width="'+bw.toFixed(1)+
         '" height="'+Math.max(1,y2-y1).toFixed(1)+'" fill="url(#'+(up?"upg":"dng")+')"/>';
  });

  function line(arr,col,w,dash){
    if(!arr) return "";
    var pth="", started=false;
    for(var i=Math.max(0,vs-1);i<vs+vc&&i<arr.length;i++){
      var v=arr[i];
      if(v==null||!isFinite(v)){ started=false; continue; }
      pth+=(started?"L":"M")+x(i).toFixed(1)+","+y(v).toFixed(1)+" ";
      started=true;
    }
    if(!pth) return "";
    return '<path d="'+pth+'" fill="none" stroke="'+col+'" stroke-width="'+w+'" opacity=".9"'+
           (dash?' stroke-dasharray="'+dash+'"':"")+'/>';
  }

  if(OV.ema){ svg+=line(IND.ema20,"#58a6ff",1.4)+line(IND.ema50,"#d29922",1.4); }

  if(OV.vwap){
    var pv=0, vv=0, cum=[];
    DATA.forEach(function(b){
      var tp=(b.h+b.l+b.c)/3;
      pv+=tp*(b.v||0); vv+=(b.v||0);
      cum.push(vv?pv/vv:null);
    });
    svg+=line(cum,"#e3b341",1.5);
  }

  if(OV.session){
    var hi=Math.max.apply(null,DATA.map(function(b){return b.h;}));
    var lo=Math.min.apply(null,DATA.map(function(b){return b.l;}));
    svg+='<line x1="'+PL+'" y1="'+y(hi).toFixed(1)+'" x2="'+(W-PR)+'" y2="'+y(hi).toFixed(1)+
         '" stroke="#3fb950" stroke-dasharray="8 5" opacity=".55"/>';
    svg+='<text x="'+(PL+6)+'" y="'+(y(hi)-4).toFixed(1)+'" fill="#3fb950" font-size="9"'+
         ' font-family="ui-monospace,monospace">session high '+hi.toPrecision(7)+'</text>';
    svg+='<line x1="'+PL+'" y1="'+y(lo).toFixed(1)+'" x2="'+(W-PR)+'" y2="'+y(lo).toFixed(1)+
         '" stroke="#f85149" stroke-dasharray="8 5" opacity=".55"/>';
    svg+='<text x="'+(PL+6)+'" y="'+(y(lo)+12).toFixed(1)+'" fill="#f85149" font-size="9"'+
         ' font-family="ui-monospace,monospace">session low '+lo.toPrecision(7)+'</text>';
  }

  if(OV.atr && IND.atr20){
    var up2="", dn2="", started2=false;
    for(var i2=Math.max(0,vs-1);i2<vs+vc&&i2<DATA.length;i2++){
      var av=IND.atr20[i2];
      if(av==null||!isFinite(av)){ started2=false; continue; }
      var cl=DATA[i2].c;
      up2+=(started2?"L":"M")+x(i2).toFixed(1)+","+y(cl+av).toFixed(1)+" ";
      dn2+=(started2?"L":"M")+x(i2).toFixed(1)+","+y(cl-av).toFixed(1)+" ";
      started2=true;
    }
    if(up2){
      svg+='<path d="'+up2+'" fill="none" stroke="#a371f7" opacity=".6"/>';
      svg+='<path d="'+dn2+'" fill="none" stroke="#a371f7" opacity=".6"/>';
    }
  }

  if(OV.rsi && IND.rsi14){
    var top=H-PB-46, bot=H-PB-6;
    var ry=function(v){ return bot-((bot-top)*(Math.max(0,Math.min(100,v))/100)); };
    svg+='<line x1="'+PL+'" y1="'+ry(70).toFixed(1)+'" x2="'+(W-PR)+'" y2="'+ry(70).toFixed(1)+
         '" stroke="#f8514966" stroke-dasharray="3 3"/>';
    svg+='<line x1="'+PL+'" y1="'+ry(30).toFixed(1)+'" x2="'+(W-PR)+'" y2="'+ry(30).toFixed(1)+
         '" stroke="#3fb95066" stroke-dasharray="3 3"/>';
    var rp="", rs=false;
    for(var ir=Math.max(0,vs-1);ir<vs+vc&&ir<IND.rsi14.length;ir++){
      var rv=IND.rsi14[ir];
      if(rv==null||!isFinite(rv)){ rs=false; continue; }
      rp+=(rs?"L":"M")+x(ir).toFixed(1)+","+ry(rv).toFixed(1)+" ";
      rs=true;
    }
    if(rp){
      svg+='<path d="'+rp+'" fill="none" stroke="#c9a0ff" stroke-width="1.3" opacity=".9"/>';
      var lastR=IND.rsi14.filter(function(v){return v!=null;}).pop();
      if(lastR!=null){
        svg+='<text x="'+(PL-8)+'" y="'+(ry(lastR)+3).toFixed(1)+'" fill="#c9a0ff" font-size="9.5"'+
             ' text-anchor="end" font-family="ui-monospace,monospace">RSI '+lastR.toFixed(0)+'</text>';
      }
    }
  }

  var last=DATA[DATA.length-1];
  svg+='<line x1="'+PL+'" y1="'+y(last.c).toFixed(1)+'" x2="'+(W-PR)+'" y2="'+y(last.c).toFixed(1)+
       '" stroke="#58a6ff" stroke-dasharray="6 4" opacity=".6"/>';
  svg+='<rect x="'+(W-PR+3)+'" y="'+(y(last.c)-10).toFixed(1)+'" width="70" height="20" rx="4"'+
       ' fill="#1b2b3d" stroke="#2f4a66"/>';
  svg+='<text x="'+(W-PR+38)+'" y="'+(y(last.c)+4).toFixed(1)+'" fill="#9ecbff" font-size="10.5"'+
       ' text-anchor="middle" font-family="ui-monospace,monospace">'+last.c.toPrecision(7)+'</text>';

  if(MEAS){
    var a=Math.min(MEAS.i0,MEAS.i1), b=Math.max(MEAS.i0,MEAS.i1);
    var x0=x(Math.max(vs,Math.min(vs+vc-1,a))), x1=x(Math.max(vs,Math.min(vs+vc-1,b)));
    var yy0=y(MEAS.p0), yy1=y(MEAS.p1);
    var dp=MEAS.p1-MEAS.p0, dpp=100*(MEAS.p1/MEAS.p0-1);
    var bars=Math.abs(b-a);
    var t0=DATA[Math.max(0,Math.min(DATA.length-1,a))].t;
    var t1=DATA[Math.max(0,Math.min(DATA.length-1,b))].t;
    var mins=Math.round(Math.abs(t1-t0)/60000);
    var col=dp>=0?"#3fb950":"#f85149";
    svg+='<rect x="'+Math.min(x0,x1).toFixed(1)+'" y="'+Math.min(yy0,yy1).toFixed(1)+'" width="'+
         Math.abs(x1-x0).toFixed(1)+'" height="'+Math.abs(yy1-yy0).toFixed(1)+
         '" fill="'+col+'14" stroke="'+col+'aa" stroke-dasharray="4 3"/>';
    var bx=Math.min(x0,x1)+10, by=Math.min(yy0,yy1)+10;
    svg+='<rect x="'+bx.toFixed(1)+'" y="'+by.toFixed(1)+'" width="186" height="56" rx="9"'+
         ' fill="#0a1118f2" stroke="'+col+'55"/>';
    svg+='<rect x="'+bx.toFixed(1)+'" y="'+by.toFixed(1)+'" width="4" height="56" rx="2" fill="'+col+'"/>';
    svg+='<text x="'+(bx+16).toFixed(1)+'" y="'+(by+22).toFixed(1)+'" fill="'+col+
         '" font-size="13" font-weight="600" font-family="ui-monospace,monospace">'+
         (dp>=0?"+":"")+dpp.toFixed(2)+'%</text>';
    svg+='<text x="'+(bx+16).toFixed(1)+'" y="'+(by+39).toFixed(1)+'" fill="#c9d6e4" font-size="10.5"'+
         ' font-family="ui-monospace,monospace">'+(dp>=0?"+":"")+dp.toPrecision(6)+'</text>';
    svg+='<text x="'+(bx+16).toFixed(1)+'" y="'+(by+51).toFixed(1)+'" fill="#5c6b7d" font-size="9.5"'+
         ' font-family="ui-monospace,monospace">'+bars+' bars · '+mins+' min</text>';
  }

  svg+='<g id="xh" opacity="0"></g>';
  $("svg").innerHTML=svg;
  $("title").textContent=S.sym;
  $("px").textContent=last.c.toPrecision(8);
  var chg=100*(last.c/DATA[0].o-1);
  $("chg").textContent=(chg>=0?"+":"")+chg.toFixed(2)+"%";
  $("chg").className="chg "+(chg>=0?"up":"down");
  $("range").textContent=S.iv+" · "+vc+"/"+n+" bars";
  $("ctlsub").textContent=S.iv+" · "+Object.keys(OV).filter(function(k){return OV[k];}).length+
    "/"+LAYERS.length+" layers";
  geom={vs:vs,vc:vc,LO:LO,HI:HI,x:x,y:y};
}

var geom=null;

function localPoint(ev){
  var svg=$("svg"), r=svg.getBoundingClientRect();
  return {x:(ev.clientX-r.left)/r.width*W, y:(ev.clientY-r.top)/r.height*H};
}
function viewX(clientX){
  var svg=$("svg"), r=svg.getBoundingClientRect();
  return (clientX-r.left)/r.width*W;
}
function zoomX(vx, count){
  var n=DATA.length, nc=Math.max(20,Math.min(n,Math.round(count)));
  var f=Math.max(0,Math.min(1,(vx-PL)/(W-PL-PR)));
  var a=(S.s||0)+f*((S.c||n)-1);
  S.c=nc; S.s=Math.max(0,Math.min(n-nc,a-f*(nc-1)));
  render();
}
function zoomY(vy, factor){
  if(!geom) return;
  var f=Math.max(0,Math.min(1,(vy-PT)/(H-PT-PB)));
  var at=geom.HI-(geom.HI-geom.LO)*f;
  var span=(geom.HI-geom.LO)*factor;
  S.y={lo:at-span*(1-f), hi:at+span*f};
  render();
}
function resetAll(){ S.s=0; S.c=null; S.y=null; MEAS=null; render(); }

function updateCross(ev){
  var g=document.getElementById("xh");
  if(!g||!geom) return;
  var p=localPoint(ev), vx=p.x, vy=p.y;
  if(vx<PL||vx>W-PR||vy<PT||vy>H-PB){ g.setAttribute("opacity","0"); return; }
  var i=Math.max(geom.vs,Math.min(geom.vs+geom.vc-1,
        geom.vs+Math.round((vx-PL)/(W-PL-PR)*(geom.vc-1))));
  var b=DATA[i]; if(!b) return;
  var price=geom.LO+(geom.HI-geom.LO)*(1-(vy-PT)/(H-PT-PB));
  var chg=100*(b.c/b.o-1), up=b.c>=b.o;
  g.setAttribute("opacity","1");
  g.innerHTML=
    '<line x1="'+vx.toFixed(1)+'" y1="'+PT+'" x2="'+vx.toFixed(1)+'" y2="'+(H-PB)+
      '" stroke="#4a5c72" stroke-dasharray="3 3"/>'+
    '<line x1="'+PL+'" y1="'+vy.toFixed(1)+'" x2="'+(W-PR)+'" y2="'+vy.toFixed(1)+
      '" stroke="#4a5c72" stroke-dasharray="3 3"/>'+
    '<rect x="'+(W-PR+3)+'" y="'+(vy-9).toFixed(1)+'" width="70" height="18" rx="4" fill="#1b2b3d" stroke="#2f4a66"/>'+
    '<text x="'+(W-PR+38)+'" y="'+(vy+4).toFixed(1)+'" fill="#9ecbff" font-size="10.5" text-anchor="middle" font-family="ui-monospace,monospace">'+price.toPrecision(7)+'</text>'+
    '<rect x="'+(Math.min(vx,W-PR-70)).toFixed(1)+'" y="'+(H-PB+5)+'" width="66" height="16" rx="4" fill="#1b2b3d" stroke="#2f4a66"/>'+
    '<text x="'+(Math.min(vx,W-PR-70)+33).toFixed(1)+'" y="'+(H-PB+16)+'" fill="#9ecbff" font-size="9.5" text-anchor="middle" font-family="ui-monospace,monospace">'+
      new Date(b.t).toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit",hour12:false})+'</text>'+
    '<rect x="'+(vx>W/2?PL+6:W-PR-226).toFixed(1)+'" y="'+(PT+6)+'" width="220" height="80" rx="8" fill="#0b131bee" stroke="#24313e"/>'+
    '<text x="'+(vx>W/2?PL+18:W-PR-214).toFixed(1)+'" y="'+(PT+32).toFixed(1)+'" fill="#e8eef6" font-size="10.5" font-family="ui-monospace,monospace">'+
      'O '+b.o.toPrecision(7)+'   H '+b.h.toPrecision(7)+'</text>'+
    '<text x="'+(vx>W/2?PL+18:W-PR-214).toFixed(1)+'" y="'+(PT+50).toFixed(1)+'" fill="#e8eef6" font-size="10.5" font-family="ui-monospace,monospace">'+
      'L '+b.l.toPrecision(7)+'   C '+b.c.toPrecision(7)+'</text>'+
    '<text x="'+(vx>W/2?PL+18:W-PR-214).toFixed(1)+'" y="'+(PT+70).toFixed(1)+'" fill="'+(up?"#3fb950":"#f85149")+'" font-size="10.5" font-family="ui-monospace,monospace">'+
      (up?"+":"")+chg.toFixed(2)+'%   vol '+(b.v>=1000?(b.v/1000).toFixed(1)+"k":b.v.toFixed(1))+'</text>';
}

function bandStart(ev){
  var g=document.getElementById("sel");
  var svg=$("svg");
  if(!g){ g=document.createElementNS("http://www.w3.org/2000/svg","g"); g.id="sel"; svg.appendChild(g); }
  var p=localPoint(ev);
  BAND={x0:p.x,y0:p.y,x1:p.x,y1:p.y};
  g.setAttribute("opacity","1");
  g.innerHTML='<rect id="selrect" x="'+p.x+'" y="'+p.y+'" width="0" height="0" fill="#58a6ff18" stroke="#58a6ff88" stroke-dasharray="3 3"/>';
}
function bandMove(ev){
  var g=document.getElementById("sel"); if(!g||!BAND) return;
  var p=localPoint(ev);
  BAND.x1=p.x; BAND.y1=p.y;
  var r=g.querySelector("#selrect"); if(!r) return;
  r.setAttribute("x",Math.min(BAND.x0,BAND.x1).toFixed(1));
  r.setAttribute("y",Math.min(BAND.y0,BAND.y1).toFixed(1));
  r.setAttribute("width",Math.abs(BAND.x1-BAND.x0).toFixed(1));
  r.setAttribute("height",Math.abs(BAND.y1-BAND.y0).toFixed(1));
}
function bandClear(){
  var g=document.getElementById("sel"); if(g){ g.innerHTML=""; g.setAttribute("opacity","0"); }
  BAND=null;
}
function bandFinish(){
  if(!BAND||!geom){ bandClear(); return; }
  var b=BAND, width=Math.abs(b.x1-b.x0), height=Math.abs(b.y1-b.y0);
  var p0=geom.LO+(geom.HI-geom.LO)*(1-(b.y0-PT)/(H-PT-PB));
  var p1=geom.LO+(geom.HI-geom.LO)*(1-(b.y1-PT)/(H-PT-PB));
  var i0=Math.round(geom.vs+(b.x0-PL)/(W-PL-PR)*(geom.vc-1));
  var i1=Math.round(geom.vs+(b.x1-PL)/(W-PL-PR)*(geom.vc-1));
  bandClear();
  if(width<8&&height<8){ return; }
  if(S.measure){
    MEAS={i0:i0,i1:i1,p0:p0,p1:p1};
    render(); return;
  }
  if(height>=8&&p1!==p0){ S.y={lo:Math.min(p0,p1),hi:Math.max(p0,p1)}; }
  if(width>=8){
    var nc=Math.max(20,Math.min(DATA.length,Math.abs(i1-i0)+1));
    S.c=nc; S.s=Math.max(0,Math.min(DATA.length-nc,Math.min(i0,i1)));
  }
  render();
}

function wire(){
  var svg=$("svg");
  svg.addEventListener("pointerdown",function(e){
    svg.setPointerCapture(e.pointerId);
    G.pts.set(e.pointerId,{x:e.clientX,y:e.clientY});
    if(G.pts.size===1){
      G.x0=e.clientX; G.y0=e.clientY; G.s0=S.s; G.c0=S.c||DATA.length;
      G.mode=e.shiftKey?"pan":"select";
      if(G.mode==="select") bandStart(e);
    }
    if(G.pts.size===2){ G.mode="pinch"; G.pinch=dist(); G.c0=S.c||DATA.length; bandClear(); }
  });
  svg.addEventListener("pointermove",function(e){
    if(G.pts.size===0) updateCross(e);
    if(!G.pts.has(e.pointerId)) return;
    G.pts.set(e.pointerId,{x:e.clientX,y:e.clientY});
    if(G.mode==="pinch"&&G.pts.size>=2&&G.pinch>0){
      var pts=Array.from(G.pts.values());
      var mid=(pts[0].x+pts[1].x)/2;
      zoomX(viewX(mid),(G.c0||DATA.length)/(dist()/G.pinch));
      return;
    }
    if(G.mode==="select"){ bandMove(e); return; }
    var dx=e.clientX-G.x0;
    if(Math.abs(dx)<1) return;
    var per=W-PL-PR, nc=S.c||DATA.length;
    var ns=Math.max(0,Math.min(DATA.length-nc,G.s0+Math.round(-dx/per*nc)));
    if(ns!==S.s){ S.s=ns; render(); }
  });
  function end(e){
    if(G.mode==="select") bandFinish();
    G.pts.delete(e.pointerId);
    if(G.pts.size<2) G.pinch=0;
  }
  svg.addEventListener("pointerup",end);
  svg.addEventListener("pointercancel",end);
  svg.addEventListener("pointerleave",function(e){
    end(e);
    var g=document.getElementById("xh"); if(g) g.setAttribute("opacity","0");
  });
  svg.addEventListener("wheel",function(e){
    e.preventDefault();
    if(e.ctrlKey||e.metaKey){ zoomX(viewX(e.clientX),(S.c||DATA.length)*Math.pow(1.0025,e.deltaY)); return; }
    if(e.shiftKey){ zoomY(localPoint(e).y, e.deltaY>0?1.1:0.91); return; }
    var trackpad=(e.deltaMode===0&&Math.abs(e.deltaY)<50);
    if(trackpad){
      var perBar=(W-PL-PR)/Math.max(1,(S.c||DATA.length)-1);
      var shift=Math.round(((e.deltaX||0)+(e.deltaY||0))/perBar);
      var ns=Math.max(0,Math.min(DATA.length-(S.c||DATA.length),(S.s||0)+shift));
      if(ns!==S.s){ S.s=ns; render(); }
      return;
    }
    zoomX(viewX(e.clientX),(S.c||DATA.length)*(e.deltaY>0?1.12:0.89));
  },{passive:false});
  svg.addEventListener("dblclick",resetAll);
  // cursor tells you which gesture you are about to perform
  svg.addEventListener("pointermove",function(e){
    if(G.pts.size) { svg.style.cursor = (G.mode==="pan") ? "grabbing" : "crosshair"; }
    else { svg.style.cursor = e.shiftKey ? "grab" : "crosshair"; }
  });
  document.addEventListener("keydown",function(e){
    if(!document.body.contains(svg)) return;
    if(e.key==="+"||e.key==="="){ zoomX((PL+W-PR)/2,(S.c||DATA.length)*0.7); e.preventDefault(); }
    else if(e.key==="-"){ zoomX((PL+W-PR)/2,(S.c||DATA.length)*1.4); e.preventDefault(); }
    else if(e.key==="0"||e.key==="r"||e.key==="R"){ resetAll(); e.preventDefault(); }
    else if(e.key==="y"||e.key==="Y"){ S.y=null; render(); e.preventDefault(); }
  });
  document.addEventListener("keydown",function(e){
    if(e.key==="Escape"){ MEAS=null; closeCtl(); render(); }
  });
}
function dist(){
  var a=Array.from(G.pts.values());
  if(a.length<2) return 0;
  return Math.hypot(a[0].x-a[1].x,a[0].y-a[1].y);
}

function renderCtl(){
  $("ivs").innerHTML=["1m","5m","15m","1h","4h","1d"].map(function(iv){
    return '<button class="chip '+(iv===S.iv?"on":"")+'" onclick="setIv(\''+iv+'\')">'+iv+'</button>';
  }).join("");
  $("layers").innerHTML=LAYERS.map(function(L){
    return '<button class="chip '+(OV[L.id]?"on":"")+'" onclick="toggleLayer(\''+L.id+'\')">'+L.label+'</button>';
  }).join("");
  $("view").innerHTML=
    '<button class="chip '+(S.measure?"armed":"")+'" onclick="toggleMeasure()">measure'+(S.measure?" ✕":"")+'</button>'+
    '<button class="chip" onclick="zoomX((PL+W-PR)/2,(S.c||DATA.length)*0.7)">zoom in</button>'+
    '<button class="chip" onclick="zoomX((PL+W-PR)/2,(S.c||DATA.length)*1.4)">zoom out</button>'+
    '<button class="chip" onclick="S.y=null;render()">reset price</button>'+
    '<button class="chip" onclick="resetAll()">reset all</button>';
}
function setIv(iv){ S.iv=iv; renderCtl(); load(); }
function toggleLayer(id){ OV[id]=!OV[id]; renderCtl(); render(); }
function toggleMeasure(){ S.measure=!S.measure; MEAS=null; renderCtl(); render(); }
function toggleCtl(){
  var el=$("ctl");
  el.classList.toggle("open");
  $("ctlbtn").textContent=el.classList.contains("open")?"✕ close":"⚙ control";
  if(el.classList.contains("open")){ renderCtl(); setTimeout(outside,0); }
}
function closeCtl(){ var el=$("ctl"); if(el){ el.classList.remove("open"); $("ctlbtn").textContent="⚙ control"; } }
function outside(){
  var el=$("ctl");
  if(!el||!el.classList.contains("open")) return;
  document.addEventListener("click",function h(ev){
    if(el.contains(ev.target)||ev.target.id==="ctlbtn") return;
    closeCtl();
    document.removeEventListener("click",h,true);
  },true);
}

wire();
renderCtl();
load();
setInterval(function(){ if(!$("ctl").classList.contains("open")) load(); }, 30000);
</script>
</body></html>"""


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html")
        if p == "/api/klines":
            try:
                with urllib.request.urlopen(BACKEND + self.path, timeout=10) as r:
                    body = json.loads(r.read().decode())
                rows = [{"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"],
                         "c": b["c"], "v": b.get("v") or 0} for b in body.get("bars") or []]
                return self._send(200, json.dumps(
                    {"rows": rows, "indicators": body.get("indicators") or {}}),
                    "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)}), "application/json")
        if p == "/health":
            return self._send(200, json.dumps({"status": "ok"}), "application/json")
        return self._send(404, json.dumps({"error": "not found"}), "application/json")

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8800)
    args = ap.parse_args()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), H)
    print("chart lab: http://127.0.0.1:%d" % args.port, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
