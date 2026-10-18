/* ui/lib.js - the shared component library. Plain JS, no framework.
   Patterns: Factory functions + composition; every component returns a string
   of markup so views stay declarative and consistent.

   UI.panel(title, body, opts)      framed section with a labelled header
   UI.table({cols, rows, key})      sortable table with consistent alignment
   UI.spark(values, w, h, colour)   inline sparkline
   UI.bar(value, max, colour)       horizontal bar, supports negatives
   UI.pill(text, tone)              status chip: ok | bad | warn | mute
   UI.stat(label, value, colour)    one number with a caption
   UI.kv(label, value, colour)      key/value row
   UI.money(n) / UI.pct(n)          number formatting
   UI.time(ms)                      HH:MM:SS
*/
window.UI = (function(){
  var F = {};

  F.esc = function(s){
    return String(s==null?"":s).replace(/[&<>"']/g, function(c){
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];
    });
  };
  F.money = function(n){
    if(n==null) return "—";
    var v = Number(n);
    return (v<0?"-":"") + "$" + Math.abs(Math.round(v)).toLocaleString();
  };
  F.pct = function(n, d){
    if(n==null) return "—";
    var v = Number(n);
    return (v>=0?"+":"") + v.toFixed(d==null?2:d) + "%";
  };
  F.time = function(ms){
    if(!ms) return "—";
    return new Date(ms).toTimeString().slice(0,8);
  };
  F.colour = function(v){ return Number(v)>=0 ? "#3fb950" : "#f85149"; };

  F.panel = function(title, body, opts){
    opts = opts || {};
    var right = opts.right || "";
    var head = title ? '<div style="display:flex;align-items:center;gap:10px;margin-bottom:9px">'+
      '<span style="font-size:10px;letter-spacing:.13em;text-transform:uppercase;color:'+
      (opts.tone||"#6b7c90")+'">'+F.esc(title)+'</span>'+
      (opts.note?'<span class="mk">'+F.esc(opts.note)+'</span>':'')+
      '<span style="flex:1"></span>'+right+'</div>' : "";
    return '<div class="panel" style="padding:'+(opts.pad||"12px 14px")+'">'+head+body+'</div>';
  };

  F.pill = function(text, tone){
    var map = {ok:"#3fb950", bad:"#f85149", warn:"#d29922", mute:"#59677a"};
    var c = map[tone||"mute"];
    return '<span style="font-family:var(--mono);font-size:9.5px;letter-spacing:.08em;'+
      'text-transform:uppercase;color:'+c+';border:1px solid '+c+'55;border-radius:9px;'+
      'padding:1px 7px">'+F.esc(text)+'</span>';
  };

  F.bar = function(value, max, colour){
    var v = Number(value)||0, m = Math.abs(Number(max)||1);
    var w = Math.max(1, Math.min(100, 100*Math.abs(v)/m));
    var c = colour || F.colour(v);
    return '<span style="display:flex;height:6px;background:#16202b;border-radius:3px;'+
      'overflow:hidden"><i style="display:block;height:100%;width:'+w.toFixed(0)+'%;background:'+c+';'+
      (v<0?'margin-left:auto':'')+'"></i></span>';
  };

  F.spark = function(values, w, h, colour){
    w = w||100; h = h||20;
    if(!values || values.length<2) return '<span style="display:inline-block;width:'+w+'px;height:'+h+
      'px;border-bottom:1px solid #1a2430"></span>';
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    var rng = (hi-lo)||1;
    var pts = values.map(function(v,i){
      var x = (i/(values.length-1))*w;
      var y = h-1-((v-lo)/rng)*(h-2);
      return x.toFixed(1)+','+y.toFixed(1);
    }).join(" ");
    return '<svg width="'+w+'" height="'+h+'" style="display:block"><polyline points="'+pts+
      '" fill="none" stroke="'+(colour||"#58a6ff")+'" stroke-width="1.1"/></svg>';
  };

  F.stat = function(label, value, colour){
    return '<div><div style="font-size:9px;letter-spacing:.12em;text-transform:uppercase;'+
      'color:#5f7186">'+F.esc(label)+'</div><div style="font-family:var(--mono);font-size:17px;'+
      'font-weight:700;color:'+(colour||"#e6edf6")+'">'+value+'</div></div>';
  };

  F.kv = function(label, value, colour){
    return '<div style="display:flex;justify-content:space-between;gap:10px;padding:3px 0;'+
      'border-bottom:1px solid #131c26;font-family:var(--mono);font-size:11.5px">'+
      '<span style="color:#7c8b9d">'+F.esc(label)+'</span>'+
      '<b style="color:'+(colour||"#e6edf6")+'">'+value+'</b></div>';
  };

  /* sortable table: click a header, sort persists across re-renders via key */
  F._sort = F._sort || {};
  F.table = function(opts){
    var cols = opts.cols||[], rows = opts.rows||[], key = opts.key||"t";
    var st = F._sort[key];
    var idx = st ? st.idx : -1, dir = st ? st.dir : 1;
    if(idx >= 0){
      rows = rows.slice().sort(function(a,b){
        var x = a[idx], y = b[idx];
        var nx = parseFloat(String(x).replace(/[$,%+]/g,"")), ny = parseFloat(String(y).replace(/[$,%+]/g,""));
        if(!isNaN(nx) && !isNaN(ny)) return dir*(nx-ny);
        return dir*String(x).localeCompare(String(y));
      });
    }
    var head = cols.map(function(c,i){
      var arrow = (i===idx) ? (dir>0?" ▲":" ▼") : "";
      return '<th data-sortkey="'+F.esc(key)+'" data-col="'+i+'" style="cursor:pointer;'+
        'text-align:'+(c.align||"right")+';white-space:nowrap">'+F.esc(c.label)+arrow+'</th>';
    }).join("");
    var body = rows.map(function(r){
      return '<tr>'+cols.map(function(c,i){
        return '<td style="text-align:'+(c.align||"right")+'">'+(r[i]==null?"—":r[i])+'</td>';
      }).join("")+'</tr>';
    }).join("");
    return '<table class="wide"><thead><tr>'+head+'</tr></thead><tbody>'+
      (body || '<tr><td colspan="'+cols.length+'" style="text-align:center;color:#59677a">no rows</td></tr>')+
      '</tbody></table>';
  };

  F.grid = function(cards, cols){
    return '<div style="display:grid;grid-template-columns:repeat('+(cols||3)+
      ',minmax(0,1fr));gap:12px">'+cards.join("")+'</div>';
  };

  /* delegated header clicks drive every table rendered by UI.table */
  document.addEventListener("click", function(e){
    var th = e.target.closest && e.target.closest("th[data-sortkey]");
    if(!th) return;
    var key = th.getAttribute("data-sortkey"), i = parseInt(th.getAttribute("data-col"),10);
    var st = F._sort[key];
    F._sort[key] = {idx:i, dir:(st && st.idx===i) ? -st.dir : -1};
    if(window.__rerender) window.__rerender();
  }, true);

  return F;
})();
