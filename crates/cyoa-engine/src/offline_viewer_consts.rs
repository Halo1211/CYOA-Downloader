//! Generated from Python injector.py / iccplus.py by Claude (do not edit by hand).
pub(crate) const CHEAT_OVERLAY: &str = r######"
<style id="__cyoa_cheat_style__">
#__cyoa_gear{position:fixed;right:10px;bottom:10px;width:32px;height:32px;border:none;border-radius:9px;
display:flex;align-items:center;justify-content:center;
background:rgba(30,36,48,0.75);color:rgba(255,255,255,0.85);
backdrop-filter:blur(8px);box-shadow:0 4px 14px rgba(0,0,0,0.3);
cursor:pointer;z-index:2147483647;font-size:18px;transition:background .15s}
#__cyoa_gear:hover{background:rgba(60,70,90,0.92)}
#__cyoa_panel{position:fixed;right:10px;bottom:50px;width:230px;padding:14px;border-radius:12px;
background:rgba(18,22,32,0.94);color:#e8eeff;
box-shadow:0 12px 32px rgba(0,0,0,0.4);z-index:2147483647;
font:12px/1.5 system-ui,sans-serif;display:none}
#__cyoa_panel.open{display:block}
.__cy_title{font-size:11px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;
color:rgba(150,170,220,.7);margin-bottom:10px}
.__cy_row{display:flex;gap:8px;margin-bottom:8px;align-items:flex-end}
.__cy_lbl{font-size:10px;color:rgba(200,215,245,.6);margin-bottom:3px}
.__cy_sel,.__cy_inp{background:rgba(255,255,255,.07);border:1px solid rgba(120,140,190,.25);
border-radius:7px;color:#f0f4ff;padding:5px 8px;font:inherit;box-sizing:border-box}
.__cy_sel{appearance:auto;flex:1.2}
.__cy_inp{flex:.8;width:0}
.__cy_btn{width:100%;margin-top:6px;background:rgba(255,255,255,.07);
border:1px solid rgba(120,140,190,.25);border-radius:7px;color:#e8eeff;
padding:7px;font:inherit;cursor:pointer;text-align:left;transition:background .12s}
.__cy_btn:hover{background:rgba(255,255,255,.14)}
.__cy_sep{border:none;border-top:1px solid rgba(120,140,190,.15);margin:8px 0}
/* Autoplay unblock banner */
#__cyoa_audio_banner{position:fixed;top:0;left:0;right:0;padding:10px 16px;
background:rgba(16,20,30,0.92);backdrop-filter:blur(6px);
color:#e8eeff;font:13px/1.4 system-ui,sans-serif;
display:flex;align-items:center;gap:12px;z-index:2147483646;
border-bottom:1px solid rgba(99,140,255,.3)}
#__cyoa_audio_banner button{background:#3b82f6;border:none;border-radius:6px;
color:#fff;padding:6px 14px;font:inherit;cursor:pointer;white-space:nowrap}
</style>
<div id="__cyoa_audio_banner" style="display:none">
  <span>🔇 Audio diblokir browser (autoplay policy)</span>
  <button onclick="__cyoaUnblockAudio()">▶ Aktifkan Audio</button>
  <span style="margin-left:auto;cursor:pointer;opacity:.6" onclick="document.getElementById('__cyoa_audio_banner').style.display='none'">✕</span>
</div>
<div id="__cyoa_panel">
<div class="__cy_title">⚡ Serve Developer Tools</div>
<div class="__cy_row">
<label style="flex:1.2"><div class="__cy_lbl">Point type</div>
<select class="__cy_sel" id="__cyoa_pt_sel"></select></label>
<label style="flex:.8"><div class="__cy_lbl">Value</div>
<input class="__cy_inp" id="__cyoa_pt_val" type="number" step="1"></label>
</div>
<button class="__cy_btn" id="__cyoa_set_pts">💰 Set Points</button>
<hr class="__cy_sep">
<button class="__cy_btn" id="__cyoa_rm_reqs">🔓 Remove All Requirements</button>
<button class="__cy_btn" id="__cyoa_unlim">♾️ Unlimited Choices (all rows)</button>
<button class="__cy_btn" id="__cyoa_sel_all">✅ Select All Choices</button>
<button class="__cy_btn" id="__cyoa_desel_all">☐ Deselect All Choices</button>
</div>
<button id="__cyoa_gear" title="Serve Developer Tools" aria-label="Serve Developer Tools">⚙</button>
<script id="__cyoa_cheat_script__">(function(){
var btn=document.getElementById('__cyoa_gear'),
    panel=document.getElementById('__cyoa_panel'),
    ptSel=document.getElementById('__cyoa_pt_sel'),
    ptVal=document.getElementById('__cyoa_pt_val'),
    audioBanner=document.getElementById('__cyoa_audio_banner');
if(!btn||!panel)return;
btn.onclick=function(){panel.classList.toggle('open');if(panel.classList.contains('open'))refresh()};

// â”€â”€ Autoplay unblock â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// ICC Plus v2 uses No() which calls audio.play() on load.
// If autoplay is blocked, we show a banner so user can explicitly unlock.
var _audioUnblocked=false;
window.__cyoaUnblockAudio=function(){
  _audioUnblocked=true;
  if(audioBanner)audioBanner.style.display='none';
  // Resume any pending audio context
  if(window.AudioContext||window.webkitAudioContext){
    try{var ac=new (window.AudioContext||window.webkitAudioContext)();ac.resume();}catch(e){}
  }
  // Force-play any paused audio elements
  document.querySelectorAll('audio').forEach(function(a){
    if(a.src&&a.paused&&a.readyState>0){a.play().catch(function(){});}
  });
  // Retry ICC Plus bgm
  var app=getApp();
  if(app&&app.bgmId&&window._cyoa_bgm_load){
    try{window._cyoa_bgm_load(app.bgmId);}catch(e){}
  }
};

// Intercept audio play failures and show banner
var _origPlay=HTMLAudioElement.prototype.play;
HTMLAudioElement.prototype.play=function(){
  var self=this, r=_origPlay.call(this);
  if(r&&typeof r.catch==='function'){
    r.catch(function(e){
      var msg=String(e);
      if(!_audioUnblocked&&audioBanner&&
         (msg.indexOf('NotAllowed')!==-1||msg.indexOf('interact')!==-1||msg.indexOf('autoplay')!==-1)){
        audioBanner.style.display='flex';
      }
    });
  }
  return r;
};

function getApp(){try{
// ICC Plus Svelte exposes window.debugApp in official/debug builds. Prefer it
// for inspection; mutation buttons still fall back gracefully if internals differ.
if(window.debugApp){
  if(window.debugApp.app&&Array.isArray(window.debugApp.app.pointTypes))return window.debugApp.app;
  if(window.debugApp.state&&window.debugApp.state.app&&Array.isArray(window.debugApp.state.app.pointTypes))return window.debugApp.state.app;
  if(Array.isArray(window.debugApp.pointTypes))return window.debugApp;
}
var s=window.app&&window.app.__vue__&&window.app.__vue__.$store&&window.app.__vue__.$store.state;
if(s&&s.app&&Array.isArray(s.app.pointTypes))return s.app;
if(window.__pinia){var stores=Object.values(window.__pinia.state.value||{});
for(var i=0;i<stores.length;i++){if(stores[i]&&Array.isArray(stores[i].pointTypes))return stores[i];}}
}catch(e){}return null;}
function refresh(){var app=getApp();if(!app)return;
var pts=app.pointTypes||[];ptSel.innerHTML='';
pts.forEach(function(p,i){if(!p)return;var o=document.createElement('option');
o.value=i;o.textContent=(p.name||('Point '+i))+' ('+Math.round(p.startingSum||0)+')';ptSel.appendChild(o);});
if(ptSel.options.length>0){var idx=parseInt(ptSel.value)||0;
ptVal.value=Math.round((pts[idx]||{}).startingSum||0);}}
ptSel.onchange=function(){var app=getApp();if(!app)return;
var idx=parseInt(ptSel.value)||0;
ptVal.value=Math.round((app.pointTypes[idx]||{}).startingSum||0);};
document.getElementById('__cyoa_set_pts').onclick=function(){var app=getApp();if(!app)return;
var idx=parseInt(ptSel.value)||0,v=parseFloat(ptVal.value);
if(!isNaN(v)&&app.pointTypes[idx]){app.pointTypes[idx].startingSum=v;refresh();}};
document.getElementById('__cyoa_rm_reqs').onclick=function(){var app=getApp();if(!app)return;
(app.rows||[]).forEach(function(r){if(!r)return;delete r.requireds;
(r.objects||[]).forEach(function(o){if(o)delete o.requireds;});});
this.textContent='✓ Requirements removed';var self=this;setTimeout(function(){self.textContent='🔓 Remove All Requirements';},1500);};
document.getElementById('__cyoa_unlim').onclick=function(){var app=getApp();if(!app)return;
(app.rows||[]).forEach(function(r){if(r)r.allowedChoices=0;});
this.textContent='✓ Done';var self=this;setTimeout(function(){self.textContent='♾️ Unlimited Choices (all rows)';},1500);};
document.getElementById('__cyoa_sel_all').onclick=function(){var app=getApp();if(!app)return;
(app.rows||[]).forEach(function(r){(r&&r.objects||[]).forEach(function(o){if(o&&o.id)o.isSelected=true;});});};
document.getElementById('__cyoa_desel_all').onclick=function(){var app=getApp();if(!app)return;
(app.rows||[]).forEach(function(r){(r&&r.objects||[]).forEach(function(o){if(o)o.isSelected=false;});});};
var t=setInterval(function(){if(getApp()){if(panel.classList.contains('open'))refresh();clearInterval(t);}},500);
setTimeout(function(){clearInterval(t);},30000);
})();</script>"######;
pub(crate) const INTERCEPTOR_HEAD: &str = r######"<script src="__cyoa_offline_project__.js"></script>
<script id="__cyoa_offline_patch__">(function(){var D=window.__CYOA_OFFLINE_PROJECT__;if(!D){console.error("Offline project payload is missing");return;}var R=/project\.json|data\.json/i;var _f=window.fetch;window.fetch=function(u,o){if(R.test(String(u||"")))return Promise.resolve(new Response(JSON.stringify(D),{status:200,headers:{"Content-Type":"application/json"}}));return _f?_f.call(this,u,o):Promise.reject(new Error("fetch N/A"));};var _X=window.XMLHttpRequest;if(_X){window.XMLHttpRequest=function(){var x=new _X(),_open=x.open.bind(x),_send=x.send.bind(x),hit=false;x.open=function(m,u){if(location.protocol==="file:"&&R.test(String(u||""))){hit=true;return;}return _open.apply(x,arguments);};x.send=function(body){if(!hit)return _send.apply(x,arguments);var text=JSON.stringify(D),total=text.length;function prop(n,v){try{Object.defineProperty(x,n,{configurable:true,get:function(){return v;}});}catch(e){}}function event(n){try{x.dispatchEvent(new ProgressEvent(n,{lengthComputable:true,loaded:total,total:total}));}catch(e){x.dispatchEvent(new Event(n));}}prop("readyState",4);prop("status",200);prop("statusText","OK");prop("responseText",text);prop("response",text);setTimeout(function(){event("progress");event("readystatechange");event("load");event("loadend");},0);};return x;};window.XMLHttpRequest.prototype=_X.prototype;}window.__CYOA_OFFLINE__=true;window.__CYOA_DATA__=D;document.addEventListener("DOMContentLoaded",function(){var el=document.getElementById("projectSize");if(el)el.textContent=""######;
pub(crate) const INTERCEPTOR_TAIL: &str = r######"";});})();</script>
"######;
