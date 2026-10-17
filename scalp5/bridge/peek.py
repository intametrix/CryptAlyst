#!/usr/bin/env python3
"""peek - a tiny viewer for the Go bridge broker on 8810."""
import json, os, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BROKER = os.environ.get("BROKER", "http://127.0.0.1:8810")
PORT = int(os.environ.get("PORT", "8811"))
LOG = os.environ.get("BRIDGE_LOG", "/Users/users1/Documents/kimi/tasks/2026-10-17/11-08-11-ea1d54fe/bridge.log")


def jget(u, t=5.0):
    with urllib.request.urlopen(u, timeout=t) as r:
        return json.loads(r.read().decode())


PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>bridge · broker peek</title>
<style>
:root{--bg:#0a0e13;--panel:#101721;--line:#1c2836;--dim:#5f7186;--text:#c9d6e3;
--dsh:#58a6ff;--kimi:#e3b341;--sys:#3fb950;--bad:#f85149}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:radial-gradient(1200px 600px at 50% -10%,#12202e 0%,var(--bg) 55%);
color:var(--text);font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;
display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:12px;padding:12px 18px;border-bottom:1px solid var(--line);
background:rgba(10,14,19,.82);backdrop-filter:blur(12px);position:sticky;top:0;z-index:10}
.brand{font-size:12px;letter-spacing:.22em;text-transform:uppercase;color:#8fa3b8;font-weight:700}
.brand b{color:var(--sys)}
.pill{border:1px solid var(--line);border-radius:999px;padding:4px 10px;font-size:11px;
color:#8fa3b8;display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.pill.ok{border-color:#1f5f3a;color:var(--sys)}.pill.bad{border-color:#6b2b2b;color:var(--bad)}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 8px currentColor}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.dot.live{animation:pulse 2s infinite}
.spacer{flex:1}
input[type=search],select{background:#0d141c;border:1px solid var(--line);color:var(--text);
border-radius:8px;padding:6px 10px;font:inherit;font-size:12px;outline:none}
input[type=search]:focus,select:focus{border-color:#2f4d6b;box-shadow:0 0 0 3px #12293d}
button{background:#12202e;border:1px solid var(--line);color:var(--text);border-radius:8px;
padding:6px 12px;font:inherit;font-size:12px;cursor:pointer;transition:.15s}
button:hover{border-color:#3d6a94;background:#16293c}
button.primary{border-color:#2f5d3f;color:var(--sys)}
main{flex:1;overflow:auto;padding:16px 18px 90px;scroll-behavior:smooth}
.msg{border:1px solid var(--line);border-left:3px solid #2a3a4a;border-radius:10px;
padding:10px 13px;margin-bottom:9px;background:linear-gradient(180deg,#101821,#0d141c);
transition:.15s}
.msg:hover{border-color:#2b3d51;transform:translateX(2px)}
.msg.dsh{border-left-color:var(--dsh)}.msg.kimi{border-left-color:var(--kimi)}
.msg.broker{border-left-color:var(--sys);opacity:.72}
.head{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--dim);margin-bottom:6px}
.who{font-weight:700;color:var(--text)}
.who.dsh{color:var(--dsh)}.who.kimi{color:var(--kimi)}.who.broker{color:var(--sys)}
.topic{border:1px solid var(--line);border-radius:6px;padding:1px 6px;font-size:10px;color:#8095ab}
.body{white-space:pre-wrap;word-break:break-word}
.body a{color:var(--dsh)}
.copy{margin-left:auto;opacity:0;font-size:10px;cursor:pointer;color:var(--dim);border:0;background:none;padding:0}
.msg:hover .copy{opacity:1}
.copy:hover{color:var(--text)}
#newbar{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);display:none;
border-color:#2f5d3f;color:var(--sys);box-shadow:0 10px 30px -10px #000}
.empty{color:var(--dim);text-align:center;padding:60px 0}
.kbd{font-size:10px;color:var(--dim);border:1px solid var(--line);border-radius:4px;padding:1px 5px}
</style></head><body>
<header>
  <span class=brank></span><span class="brand">bridge <b>peek</b></span>
  <span class=pill id=st><i class="dot live"></i>connecting</span>
  <span class=pill id=cn>0 messages</span>
  <span class=pill id=rt>—</span>
  <span id=agents style="display:flex;gap:6px"></span>
  <span class=spacer></span>
  <input type=search id=q placeholder="search  /" autocomplete=off>
  <select id=f>
    <option value=all>all senders</option>
    <option value=dsh>dsh</option>
    <option value=kimi>kimi</option>
    <option value=broker>broker</option>
  </select>
  <button id=pause title="pause (space)">⏸</button>
</header>
<main id=list><div class=empty>waiting for messages…</div></main>
<button id=newbar>↓ jump to latest</button>
<script>
var PAUSED=false, LAST=0, AUTO=true, ALL=[];
var list=document.getElementById('list');
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
function rel(ts){var s=(Date.now()-ts)/1000;
  if(s<60)return Math.round(s)+'s ago'; if(s<3600)return Math.round(s/60)+'m ago';
  if(s<86400)return Math.round(s/3600)+'h ago'; return Math.round(s/86400)+'d ago';}
function hhmm(ts){return new Date(ts).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});}
function render(){
  var q=(document.getElementById('q').value||'').toLowerCase();
  var f=document.getElementById('f').value;
  var ms=ALL.filter(function(m){
    if(f!=='all'&&m.from!==f)return false;
    if(q&&JSON.stringify(m).toLowerCase().indexOf(q)<0)return false;
    return true;});
  if(!ms.length){list.innerHTML='<div class=empty>no messages match</div>';return;}
  var near=list.scrollHeight-list.scrollTop-list.clientHeight<80;
  list.innerHTML=ms.slice(-400).map(function(m){
    var c=m.from==='dsh'?'dsh':(m.from==='kimi'?'kimi':'broker');
    return '<div class="msg '+c+'" id="m'+m.id+'"><div class=head><span class="who '+c+'">'+m.from+
      '</span><span>→ '+m.to+'</span><span class=topic>'+esc(m.topic)+'</span><span>'+hhmm(m.ts)+
      '</span><span style="color:var(--dim)">'+rel(m.ts)+'</span>'+
      '<button class=copy onclick="cp(\''+m.id+'\')">copy</button></div>'+
      '<div class=body>'+esc(m.body)+'</div></div>';
  }).join('');
  if(AUTO&&near) list.scrollTop=list.scrollHeight;
}
function cp(id){var m=ALL.filter(function(x){return String(x.id)===String(id);})[0];
  if(m&&navigator.clipboard) navigator.clipboard.writeText(m.body);}
async function tick(){
  if(PAUSED) return;
  try{
    var r=await fetch('/api/peek',{cache:'no-store'});var d=await r.json();
    var st=document.getElementById('st');
    st.className='pill '+(d.ok?'ok':'bad');
    st.innerHTML='<i class="dot '+(d.ok?'live':'')+'"></i>'+(d.ok?'broker ok':'broker down');
    ALL=d.messages||[];
    document.getElementById('cn').textContent=ALL.length+' messages';
    var last=ALL.length?ALL[ALL.length-1].ts:0;
    document.getElementById('rt').textContent=last?rel(last):'—';
    var pres={};
    ALL.forEach(function(m){ if(m.topic!=='presence')return;
      var j=m.body.match(/just joined\s+(\S+)/i); if(j){pres[j[1]]=1;return;}
      var l=m.body.match(/^(\S+)\s+left/i); if(l){pres[l[1]]=0;return;}});
    var ks=Object.keys(pres);
    document.getElementById('agents').innerHTML=ks.length?ks.map(function(k){
      return '<span class="pill '+(pres[k]?'ok':'bad')+'" title="'+(pres[k]?'connected':'left')+'">'+
        '<i class="dot '+(pres[k]?'live':'')+'"></i>'+k+'</span>';}).join('')
      :'<span class=pill>no agents</span>';
    if(ALL.length!==LAST){LAST=ALL.length;render();}
  }catch(e){
    var st=document.getElementById('st');st.className='pill bad';st.innerHTML='<i class=dot></i>broker unreachable';
  }
}
document.getElementById('q').addEventListener('input',render);
document.getElementById('f').addEventListener('change',render);
document.getElementById('pause').onclick=function(){PAUSED=!PAUSED;
  this.textContent=PAUSED?'▶':'⏸';this.classList.toggle('primary',PAUSED);};
document.getElementById('newbar').onclick=function(){AUTO=true;list.scrollTop=list.scrollHeight;
  this.style.display='none';};
list.addEventListener('scroll',function(){
  var near=list.scrollHeight-list.scrollTop-list.clientHeight<80;
  AUTO=near; document.getElementById('newbar').style.display=near?'none':'block';});
document.addEventListener('keydown',function(e){
  if(e.key==='/'&&document.activeElement.tagName!=='INPUT'){e.preventDefault();
    document.getElementById('q').focus();}
  if(e.key===' '&&document.activeElement.tagName!=='INPUT'){e.preventDefault();
    document.getElementById('pause').click();}});
tick();setInterval(tick,1000);
</script></body></html>"""


AGENTS = {}   # name -> {"ip":..., "ts":...}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        import json as _j
        if self.path.startswith("/api/clear"):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = _j.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
            # the broker has no /clear, so a real clear = truncate its log and
            # restart it; it replays history from disk, so an empty log means empty
            try:
                import signal, subprocess
                logp = os.environ.get("BRIDGE_LOG", "/Users/users1/Documents/kimi/tasks/"
                                      "2026-10-17/11-08-11-ea1d54fe/bridge.log")
                subprocess.run(["pkill", "-f", "11-08-11-ea1d54fe/bridge"], check=False)
                time.sleep(0.7)
                open(logp, "w").close()
                binp = os.environ.get("BRIDGE_BIN", "/Users/users1/Documents/kimi/tasks/"
                                      "2026-10-17/11-08-11-ea1d54fe/bridge")
                env = dict(os.environ, PORT="8810")
                subprocess.Popen([binp], cwd=os.path.dirname(binp), env=env,
                                 stdout=open(os.path.join(os.path.dirname(binp), "bridge.out"), "ab"),
                                 stderr=subprocess.STDOUT, start_new_session=True)
                time.sleep(1.2)
                try:
                    jget(BROKER + "/hello", timeout=4)
                    up = True
                except Exception:
                    up = False
                return self._send(200, _j.dumps({"ok": up, "mode": "log truncated, broker restarted",
                                                 "cleared": True}))
            except Exception as exc:
                # broker has no /clear: keep a local high-water mark instead
                try:
                    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "cleared_at"), "w") as fh:
                        fh.write(str(int(time.time() * 1000)))
                except Exception:
                    pass
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "cleared_at"), "w") as fh:
                    fh.write(str(int(time.time() * 1000)))
                return self._send(200, _j.dumps({"ok": True, "mode": "local high-water mark",
                                                 "note": "broker has no /clear; peek hides older messages"}))
        if self.path.startswith("/api/say"):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = _j.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
            payload = _j.dumps({"from": body.get("from") or "user",
                                "to": body.get("to") or "all",
                                "topic": body.get("topic") or "general",
                                "body": body.get("body") or ""}).encode()
            try:
                req = urllib.request.Request(BROKER + "/say", data=payload,
                                             headers={"Content-Type": "application/json"})
                out = urllib.request.urlopen(req, timeout=6).read().decode()
                return self._send(200, out)
            except Exception as exc:
                return self._send(502, _j.dumps({"ok": False, "error": str(exc)[:120]}))
        if self.path.startswith("/api/register"):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = _j.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
            name = (body.get("name") or "").strip()
            if not name:
                return self._send(200, _j.dumps({"ok": False, "error": "name required"}))
            AGENTS[name] = {"ip": self.client_address[0], "ts": int(time.time() * 1000),
                            "ua": (self.headers.get("User-Agent") or "")[:60]}
            return self._send(200, _j.dumps({"ok": True, "name": name,
                                             "ip": self.client_address[0]}))
        return self._send(404, _j.dumps({"error": "not found"}))

    def do_GET(self):
        if self.path.startswith("/api/agents"):
            return self._send(200, json.dumps({"agents": AGENTS, "port": PORT}))
        if self.path.startswith("/api/peek"):
            try:
                # the broker filters /log by recipient, so ask for every mailbox
                seen, ms = set(), []
                for box in ("dsh", "kimi", "all", "broker", "user"):
                    try:
                        d = jget(BROKER + "/log?to=%s&since=0" % box)
                    except Exception:
                        continue
                    for m in d.get("messages") or []:
                        if m.get("id") in seen:
                            continue
                        seen.add(m.get("id"))
                        ms.append(m)
                ms.sort(key=lambda x: x.get("id", 0))
                cut = 0
                try:
                    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "cleared_at")) as fh:
                        cut = int((fh.read() or "0").strip() or 0)
                except Exception:
                    cut = 0
                if cut:
                    ms = [m for m in ms if (m.get("ts") or 0) > cut]
                return self._send(200, json.dumps({"ok": True, "messages": ms, "age_ms": 0,
                                                   "cleared_at": cut}))
            except Exception as exc:
                return self._send(200, json.dumps({"ok": False, "messages": [],
                                                   "error": str(exc)[:120]}))
        if self.path in ("/", "/index.html"):
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "peek.html"), "rb") as fh:
                return self._send(200, fh.read(), "text/html")
        return self._send(404, json.dumps({"error": "not found"}))


if __name__ == "__main__":
    print("broker peek on http://127.0.0.1:%d  (broker %s)" % (PORT, BROKER), flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
