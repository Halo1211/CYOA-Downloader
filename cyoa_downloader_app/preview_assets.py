"""Local preview/serve helpers and bundled userscript metadata.

Phase 10 starts de-legacy extraction by moving the large bundled userscript
metadata/report out of legacy.py. Runtime preview state still delegates lazily
to legacy until the full serve subsystem is extracted.
"""

from __future__ import annotations

from typing import Any

from .app_info import _APP_VERSION


_INT_CYOA_ENHANCER_INFO = {
    "name": "IntCyoaEnhancer",
    "author": "agreg",
    "version_seen": "0.7",
    "bundled_helper_version": "7.5.0-native-cheat",
    "license": "MIT",
    "source_url": "https://greasyfork.org/en/scripts/438947-intcyoaenhancer",
    "raw_url": "https://update.greasyfork.org/scripts/438947/IntCyoaEnhancer.user.js",
    "credit": "IntCyoaEnhancer by agreg, MIT License, GreasyFork script 438947",
    "bundled_policy": "Bundled localhost helper with native Cheat Panel; no network download required for /__userscripts__/intcyoaenhancer.user.js",
}


_BUNDLED_INTCYOAENHANCER_USERSCRIPT = r"""
// ==UserScript==
// @name         IntCyoaEnhancer bundled localhost helper + Native Cheat Panel
// @namespace    cyoa-downloader.local
// @version      7.5.0-local
// @description  Bundled Serve-only helper for downloaded/offline CYOAs. Includes visible native Cheat Panel so no Tampermonkey menu is required.
// @author       CYOA Downloader integration; credit retained for IntCyoaEnhancer by agreg
// @license      MIT-compatible integration wrapper; external credit retained
// @match        http://127.0.0.1:*/*
// @match        http://localhost:*/*
// @run-at       document-idle
// ==/UserScript==
(function(){
  'use strict';
  if (window.__CYOA_BUNDLED_INTCYOAENHANCER_V750__) return;
  window.__CYOA_BUNDLED_INTCYOAENHANCER_V750__ = true;

  const CREDIT = 'Credit: IntCyoaEnhancer by agreg (MIT, GreasyFork script 438947). Native localhost Cheat Panel bundled by CYOA Downloader.';
  const SOURCE = 'https://greasyfork.org/en/scripts/438947-intcyoaenhancer';
  const PANEL_ID = 'cyoa-bundled-ice-panel';
  const CHEAT_ID = 'cyoa-bundled-ice-cheat-panel';
  const STYLE_ID = 'cyoa-bundled-ice-style';
  const ORIGINALS = { points:new Map(), req:new WeakMap(), selectable:new WeakMap(), limits:new WeakMap() };

  function log(){ try { console.log.apply(console, ['[Bundled ICE Cheat]'].concat([].slice.call(arguments))); } catch(_){} }
  function $(s, root){ return Array.prototype.slice.call((root||document).querySelectorAll(s)); }
  function safeJson(v){ try { return JSON.stringify(v, null, 2); } catch(e){ return String(v); } }
  function dl(name, text, type){ const blob=new Blob([text],{type:type||'application/json'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=name; document.documentElement.appendChild(a); a.click(); setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove();},1000); }
  function css(){ if(document.getElementById(STYLE_ID)) return; const st=document.createElement('style'); st.id=STYLE_ID; st.textContent = `
#${PANEL_ID},#${CHEAT_ID}{all:initial!important;font-family:system-ui,-apple-system,Segoe UI,sans-serif!important;color:#e5e7eb!important;box-sizing:border-box!important}
#${PANEL_ID} * ,#${CHEAT_ID} *{box-sizing:border-box!important;font-family:inherit!important}
#${PANEL_ID}{position:fixed!important;top:72px!important;right:12px!important;z-index:2147483647!important;width:310px!important;background:#0f172a!important;border:2px solid #22c55e!important;border-radius:16px!important;box-shadow:0 20px 60px #0009!important;padding:12px!important;line-height:1.35!important}
#${PANEL_ID} h3{margin:0 0 8px!important;color:#bbf7d0!important;font-size:15px!important;font-weight:900!important}
#${PANEL_ID} .grid{display:grid!important;grid-template-columns:1fr 1fr!important;gap:7px!important}
#${PANEL_ID} button,#${CHEAT_ID} button,#${CHEAT_ID} input,#${CHEAT_ID} textarea{font:12px system-ui!important;border-radius:9px!important;border:1px solid #334155!important;padding:7px 8px!important;background:#1f2937!important;color:#f8fafc!important;cursor:pointer!important}
#${PANEL_ID} button.primary,#${CHEAT_ID} button.primary{background:#047857!important;border-color:#34d399!important;color:white!important;font-weight:800!important}
#${PANEL_ID} button.accent,#${CHEAT_ID} button.accent{background:#1d4ed8!important;border-color:#60a5fa!important;color:white!important;font-weight:800!important}
#${PANEL_ID} button.danger,#${CHEAT_ID} button.danger{background:#7f1d1d!important;border-color:#fca5a5!important;color:white!important}
#${PANEL_ID} .note,#${CHEAT_ID} .note{font-size:11px!important;color:#9ca3af!important;margin-top:9px!important;line-height:1.35!important}
#${CHEAT_ID}{position:fixed!important;left:14px!important;top:14px!important;z-index:2147483647!important;width:min(720px,calc(100vw - 28px))!important;max-height:calc(100vh - 28px)!important;overflow:auto!important;background:#0b1120!important;border:2px solid #60a5fa!important;border-radius:18px!important;box-shadow:0 20px 70px #000b!important;padding:14px!important;line-height:1.45!important}
#${CHEAT_ID} h2{margin:0 0 4px!important;color:#bfdbfe!important;font-size:18px!important;font-weight:900!important}
#${CHEAT_ID} h3{margin:16px 0 8px!important;color:#bbf7d0!important;font-size:14px!important;font-weight:850!important}
#${CHEAT_ID} .top{display:flex!important;gap:8px!important;align-items:center!important;justify-content:space-between!important;position:sticky!important;top:0!important;background:#0b1120!important;padding-bottom:10px!important;border-bottom:1px solid #334155!important}
#${CHEAT_ID} .bar{display:flex!important;flex-wrap:wrap!important;gap:6px!important;margin:9px 0!important}
#${CHEAT_ID} table{width:100%!important;border-collapse:collapse!important;font-size:12px!important;margin:8px 0!important;color:#e5e7eb!important}
#${CHEAT_ID} td,#${CHEAT_ID} th{border-bottom:1px solid #334155!important;padding:6px!important;text-align:left!important;vertical-align:middle!important}
#${CHEAT_ID} th{color:#93c5fd!important;background:#111827!important;position:sticky!important;top:44px!important}
#${CHEAT_ID} input[type=number]{width:90px!important;background:#111827!important}
#${CHEAT_ID} .small{font-size:11px!important;color:#9ca3af!important}
#${CHEAT_ID} .status{padding:8px!important;border-radius:10px!important;background:#111827!important;border:1px solid #334155!important;margin:8px 0!important;white-space:pre-wrap!important}
.cyoa-ice-reveal [hidden]{display:block!important;visibility:visible!important;opacity:1!important}.cyoa-ice-reveal .hidden,.cyoa-ice-reveal .disabled,.cyoa-ice-reveal .locked,.cyoa-ice-reveal .unavailable{visibility:visible!important;opacity:1!important;filter:none!important;pointer-events:auto!important}
`; document.head.appendChild(st); }

  function toast(msg){ try{console.info('[Bundled ICE Cheat]',msg)}catch(_){} const old=document.getElementById('cyoa-ice-toast'); if(old)old.remove(); const el=document.createElement('div'); el.id='cyoa-ice-toast'; el.style.cssText='position:fixed;right:16px;top:16px;z-index:2147483647;background:#111827;color:white;border:1px solid #60a5fa;border-radius:10px;padding:9px 12px;font:12px system-ui;box-shadow:0 12px 34px #0008;max-width:360px'; el.textContent=msg; document.documentElement.appendChild(el); setTimeout(()=>el.remove(),2800); }

  function candidates(){ const out=[]; const names=['debugApp','app','project','projectData','projectJson','cyoa','store','state','__APP__','$store','$state']; for(const n of names){try{ if(window[n]) out.push([n,window[n]]); }catch(_){}} return out; }
  function normalizeApp(o){ if(!o||typeof o!=='object') return null; if(Array.isArray(o.rows)) return o; if(o.app && Array.isArray(o.app.rows)) return o.app; if(o.state && o.state.app && Array.isArray(o.state.app.rows)) return o.state.app; if(o.$store && o.$store.state && o.$store.state.app && Array.isArray(o.$store.state.app.rows)) return o.$store.state.app; if(o.__vue__ && o.__vue__.$store && o.__vue__.$store.state && o.__vue__.$store.state.app) return o.__vue__.$store.state.app; return null; }
  function findProjectLike(){ const seen=new WeakSet(); const roots=candidates(); let best=null,bestName='',bestScore=0; function score(o){ if(!o||typeof o!=='object')return 0; let s=0; if(Array.isArray(o.rows))s+=6; if(Array.isArray(o.pointTypes))s+=3; if(Array.isArray(o.backpack))s+=2; if(o.title||o.name||o.projectTitle)s+=1; return s; } const q=roots.map(x=>[x[0],x[1],0]); while(q.length && q.length<40000){ const [name,val,depth]=q.shift(); if(!val||typeof val!=='object'||seen.has(val)) continue; seen.add(val); const app=normalizeApp(val)||val; const sc=score(app); if(sc>bestScore){best=app;bestName=name;bestScore=sc} if(depth>=4) continue; let keys=[]; try{keys=Object.keys(val).slice(0,90)}catch(_){} for(const k of keys){const ch=val[k]; if(ch&&typeof ch==='object')q.push([name+'.'+k,ch,depth+1]);} } return {name:bestName,value:best,score:bestScore}; }
  function app(){ return findProjectLike().value; }
  function rows(){ const a=app(); return (a&&Array.isArray(a.rows))?a.rows:[]; }
  function items(){ return rows().flatMap(r=>Array.isArray(r.objects)?r.objects:[]); }
  function pointTypes(){ const a=app(); return (a&&Array.isArray(a.pointTypes))?a.pointTypes:[]; }
  function storeOriginals(){ pointTypes().forEach(p=>{ if(p && p.id!=null && !ORIGINALS.points.has(p.id)) ORIGINALS.points.set(p.id, Number(p.startingSum||0)); }); items().forEach(o=>{ if(o && !ORIGINALS.req.has(o)) ORIGINALS.req.set(o, JSON.stringify({requireds:o.requireds, scores:o.scores&&o.scores.map(s=>s&&s.requireds), isNotSelectable:o.isNotSelectable, isSelectable:o.isSelectable, allowedChoicesChange:o.allowedChoicesChange})); }); }
  function setPoint(id, value){ const p=pointTypes().find(x=>String(x.id)===String(id)); if(!p) throw new Error('Point not found: '+id); p.startingSum = Number(value)||0; return p.startingSum; }
  function addPoint(id, delta){ const p=pointTypes().find(x=>String(x.id)===String(id)); if(!p) throw new Error('Point not found: '+id); p.startingSum = Number(p.startingSum||0) + Number(delta||0); return p.startingSum; }
  function resetPoints(){ pointTypes().forEach(p=>{ if(p && ORIGINALS.points.has(p.id)) p.startingSum=ORIGINALS.points.get(p.id); }); renderCheat(); toast('Points reset to first detected values'); }
  function unlockRequirements(){ storeOriginals(); rows().forEach(r=>{ try{delete r.allowedChoicesChange; r.isEditModeOn=false;}catch(_){}; (r.objects||[]).forEach(o=>{ try{o.isNotSelectable=false;o.isSelectable=true;o.requireds=[];}catch(_){}; (o.scores||[]).forEach(s=>{try{s.requireds=[];}catch(_){}}); }); }); document.documentElement.classList.add('cyoa-ice-reveal'); $('button:disabled,input:disabled,select:disabled,textarea:disabled').forEach(el=>{try{el.disabled=false;el.removeAttribute('disabled')}catch(_){}}); toast('Requirements/disabled UI softened for this preview'); }
  function selectAllSoft(){ const all=items(); all.forEach(o=>{try{o.isActive=true;o.selected=true;o.isSelected=true;if(o.multipleUseVariable!=null && Number(o.multipleUseVariable)===0)o.multipleUseVariable=1;}catch(_){}}); $('.choice,.object,.choice-card,[class*=choice-]').slice(0,2000).forEach(el=>{try{ if(!el.classList.contains('choice-selected')) el.click(); }catch(_){}}); toast('Select-all soft pass executed. Reload to undo if viewer state misbehaves.'); }
  function restoreRequirements(){ items().forEach(o=>{ const raw=ORIGINALS.req.get(o); if(!raw)return; try{const v=JSON.parse(raw); if('requireds' in v)o.requireds=v.requireds; if('isNotSelectable' in v)o.isNotSelectable=v.isNotSelectable; if('isSelectable' in v)o.isSelectable=v.isSelectable; if('allowedChoicesChange' in v)o.allowedChoicesChange=v.allowedChoicesChange; if(Array.isArray(v.scores)&&Array.isArray(o.scores))o.scores.forEach((s,i)=>{if(s&&v.scores[i]!==undefined)s.requireds=v.scores[i]});}catch(_){}}); document.documentElement.classList.remove('cyoa-ice-reveal'); toast('Original requirement snapshot restored where available'); renderCheat(); }
  function downloadProjectData(){ const f=findProjectLike(); dl('cyoa-current-project-or-state.json', safeJson({source:f.name, score:f.score, exportedAt:new Date().toISOString(), data:f.value||null})); return f; }
  function exportLocalStorage(){ const data={}; for(let i=0;i<localStorage.length;i++){ const k=localStorage.key(i); data[k]=localStorage.getItem(k); } dl('cyoa-localStorage-export.json', safeJson({exportedAt:new Date().toISOString(), origin:location.origin, localStorage:data})); }
  async function exportIndexedDB(){ if(!('indexedDB' in window)) throw new Error('IndexedDB unsupported'); let dbNames=[]; if(indexedDB.databases){try{dbNames=(await indexedDB.databases()).map(d=>d.name).filter(Boolean)}catch(_){}} if(!dbNames.length)dbNames=['cyoaPlusDB','cyoaDB','ICCPlus','intcyoa']; const result={exportedAt:new Date().toISOString(),origin:location.origin,databases:{}}; const openDB=name=>new Promise((res,rej)=>{const r=indexedDB.open(name);r.onerror=()=>rej(r.error);r.onsuccess=()=>res(r.result)}); for(const name of dbNames){try{const db=await openDB(name); result.databases[name]={}; for(const st of Array.from(db.objectStoreNames||[])){result.databases[name][st]=await new Promise(resolve=>{try{const tx=db.transaction(st,'readonly'); const req=tx.objectStore(st).getAll(); req.onsuccess=()=>resolve(req.result||[]); req.onerror=()=>resolve([]);}catch(_){resolve([])}})} db.close();}catch(e){result.databases[name]={error:String(e)}}} dl('cyoa-indexeddb-export.json', safeJson(result)); }
  function revealDisabledUI(){ document.documentElement.classList.toggle('cyoa-ice-reveal'); $('button:disabled,input:disabled,select:disabled,textarea:disabled').forEach(el=>{try{el.disabled=false;el.removeAttribute('disabled')}catch(_){}}); toast('Reveal UI toggled'); }
  function clearPreviewStorage(){ try{localStorage.clear();sessionStorage.clear()}catch(_){}; if('caches'in window)caches.keys().then(keys=>keys.forEach(k=>caches.delete(k))).catch(()=>{}); if('serviceWorker'in navigator)navigator.serviceWorker.getRegistrations().then(rs=>rs.forEach(r=>r.unregister())).catch(()=>{}); alert('Preview storage/cache clear requested. Reload the page if needed.'); }

  function renderCheat(){ css(); storeOriginals(); let d=document.getElementById(CHEAT_ID); if(!d){ d=document.createElement('div'); d.id=CHEAT_ID; document.documentElement.appendChild(d); } const f=findProjectLike(); const pts=pointTypes(); const rs=rows(); const its=items(); let pointRows = pts.length ? pts.map(p=>`<tr><td><code>${escapeHtml(p.id)}</code><div class="small">${escapeHtml(p.name||p.beforeText||'')}</div></td><td><input type="number" data-pt="${escapeAttr(p.id)}" value="${Number(p.startingSum||0)}"></td><td><button data-add="${escapeAttr(p.id)}" data-d="-100">-100</button> <button data-add="${escapeAttr(p.id)}" data-d="-10">-10</button> <button data-add="${escapeAttr(p.id)}" data-d="-1">-1</button> <button data-add="${escapeAttr(p.id)}" data-d="1">+1</button> <button data-add="${escapeAttr(p.id)}" data-d="10">+10</button> <button data-add="${escapeAttr(p.id)}" data-d="100">+100</button></td></tr>`).join('') : '<tr><td colspan="3">No pointTypes found yet. Try opening the CYOA page fully, then click Refresh Detect.</td></tr>'; d.innerHTML = `<div class="top"><div><h2>🧩 Bundled ICE Cheat Panel</h2><div class="small">Native localhost panel inspired by IntCyoaEnhancer. No Tampermonkey menu needed.</div></div><button class="danger" data-close="1">Close</button></div><div class="status">Detected source: ${escapeHtml(f.name||'-')} | score: ${f.score||0}\nRows: ${rs.length} | Choices/objects: ${its.length} | Point types: ${pts.length}</div><div class="bar"><button class="primary" data-refresh="1">Refresh Detect</button><button class="accent" data-unlock="1">Unlock Requirements</button><button class="accent" data-selectall="1">Soft Select All</button><button data-restore="1">Restore Locks</button><button data-resetpts="1">Reset Points</button><button data-project="1">Download State</button><button data-ls="1">Export LS</button><button data-idb="1">Export IDB</button><button data-reveal="1">Reveal UI</button></div><h3>Point editor</h3><table><thead><tr><th>Point type</th><th>Value</th><th>Quick add</th></tr></thead><tbody>${pointRows}</tbody></table><div class="note">${escapeHtml(CREDIT)}<br>Source: ${escapeHtml(SOURCE)}<br>These changes affect only this localhost browser preview. Reload or clear storage to undo persistent local state.</div>`; d.onclick=function(ev){ const t=ev.target; if(!t)return; try{ if(t.dataset.close)d.remove(); if(t.dataset.refresh)renderCheat(); if(t.dataset.unlock)unlockRequirements(); if(t.dataset.selectall)selectAllSoft(); if(t.dataset.restore)restoreRequirements(); if(t.dataset.resetpts)resetPoints(); if(t.dataset.project)downloadProjectData(); if(t.dataset.ls)exportLocalStorage(); if(t.dataset.idb)exportIndexedDB(); if(t.dataset.reveal)revealDisabledUI(); if(t.dataset.add){addPoint(t.dataset.add,Number(t.dataset.d));renderCheat();} }catch(e){console.error(e);alert(String(e));} }; d.onchange=function(ev){ const t=ev.target; if(t&&t.dataset&&t.dataset.pt){try{setPoint(t.dataset.pt,t.value);toast('Point updated: '+t.dataset.pt+' = '+t.value)}catch(e){alert(String(e))}}}; try{ var _top=d.querySelector('.top'); if(_top && !d._dragWired){ _iceDrag(d,_top); d._dragWired=true; } }catch(_){} }
  function escapeHtml(s){ return String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
  function escapeAttr(s){ return escapeHtml(s).replace(/"/g,'&quot;'); }
  function _iceDrag(panel, handle){ try{ var sx=0,sy=0,ox=0,oy=0,drag=false; handle.style.cursor='move'; handle.addEventListener('pointerdown',function(e){ if(e.target&&e.target.tagName==='BUTTON')return; drag=true; var r=panel.getBoundingClientRect(); ox=r.left; oy=r.top; sx=e.clientX; sy=e.clientY; panel.style.setProperty('right','auto','important'); panel.style.setProperty('left',ox+'px','important'); panel.style.setProperty('top',oy+'px','important'); try{handle.setPointerCapture(e.preventDefault?e.pointerId:0);}catch(_){} e.preventDefault(); },true); window.addEventListener('pointermove',function(e){ if(!drag)return; var nx=ox+(e.clientX-sx), ny=oy+(e.clientY-sy); nx=Math.max(0,Math.min(nx,window.innerWidth-60)); ny=Math.max(0,Math.min(ny,window.innerHeight-30)); panel.style.setProperty('left',nx+'px','important'); panel.style.setProperty('top',ny+'px','important'); }); window.addEventListener('pointerup',function(){drag=false;}); }catch(_){} }
  function showPanel(){ var d=document.getElementById(PANEL_ID); if(d){ d.style.setProperty('display','block','important'); return d; } return panel(true); }
  function panel(force){ css(); let d=document.getElementById(PANEL_ID); if(d && !force){ d.remove(); return; } if(d) d.remove(); d=document.createElement('div'); d.id=PANEL_ID; d.innerHTML='<h3 data-drag="1">🧩 Bundled ICE Helper <span style="float:right;display:flex;gap:6px"><button data-a="min" title="Minimize" style="padding:2px 8px!important">—</button></span></h3><div class="grid"><button class="primary" data-a="cheat">Open Cheat Panel</button><button data-a="project">Download State</button><button data-a="ls">Export localStorage</button><button data-a="idb">Export IndexedDB</button><button data-a="reveal">Reveal UI</button><button data-a="unlock">Unlock Reqs</button><button data-a="clear">Clear storage</button><button data-a="close">Close</button></div><div class="note">'+CREDIT+'<br>Cheat is native in this bundled route, so it does not need the Tampermonkey menu.</div>'; d.onclick=function(ev){const a=ev.target&&ev.target.dataset&&ev.target.dataset.a; try{ if(a==='cheat')renderCheat(); if(a==='project')downloadProjectData(); if(a==='ls')exportLocalStorage(); if(a==='idb')exportIndexedDB(); if(a==='reveal')revealDisabledUI(); if(a==='unlock')unlockRequirements(); if(a==='clear')clearPreviewStorage(); if(a==='min')d.style.setProperty('display','none','important'); if(a==='close')d.style.setProperty('display','none','important'); }catch(e){console.error(e);alert(String(e));}}; document.documentElement.appendChild(d); var h=d.querySelector('[data-drag]'); if(h) _iceDrag(d,h); return d; }
  const api={credit:CREDIT,source:SOURCE,candidates,findProjectLike,app,rows,items,pointTypes,openPanel:showPanel,showPanel,openCheat:renderCheat,downloadProjectData,exportLocalStorage,exportIndexedDB,revealDisabledUI,unlockRequirements,selectAllSoft,restoreRequirements,clearPreviewStorage,addPoint,setPoint,resetPoints}; window.$dbg=Object.assign(window.$dbg||{},api); window.$serveTools=Object.assign(window.$serveTools||{},api); window.IntCyoaEnhancerBundled=api; window.IntCyoaEnhancerCheat=api;
  setTimeout(()=>{ toast('Bundled cheat ready. Use the Serve Tools panel → Load Cheat.'); },700);
  log('loaded with native Cheat Panel. Use window.IntCyoaEnhancerCheat.openCheat().', CREDIT);
})();
"""


def userscript_integration_report() -> str:
    """Return human-readable credit and usage notes for optional Serve-only userscripts."""
    info = _INT_CYOA_ENHANCER_INFO
    lines = [
        f"CYOA Downloader v{_APP_VERSION} userscript integration",
        "=" * 58,
        f"Name       : {info.get('name', '-')}",
        f"Author     : {info.get('author', '-')}",
        f"Version    : {info.get('version_seen', '-')}",
        f"License    : {info.get('license', '-')}",
        f"Source     : {info.get('source_url', '-')}",
        f"Remote JS  : {info.get('raw_url', '-')}",
        f"Credit     : {info.get('credit', '-')}",
        "",
        "Policy",
        "------",
        "- Downloaded CYOA output files are never modified by the userscript integration.",
        "- Serve preview exposes a bundled localhost helper at /__userscripts__/intcyoaenhancer.user.js.",
        "- The bundled helper includes a visible Native Cheat Panel; no Tampermonkey menu is required.",
        "- Local .user.js files can still override the bundled helper for advanced testing.",
        "- Local file lookup order: served CYOA folder, userscripts/, serve_userscripts/.",
        "- Native Serve Tools remain available as a fallback through window.$serveTools.",
        "- Use this for localhost/offline debugging, accessibility, and QoL testing only.",
    ]
    return "\n".join(lines) + "\n"



__all__ = [
    "_INT_CYOA_ENHANCER_INFO",
    "_BUNDLED_INTCYOAENHANCER_USERSCRIPT",
    "userscript_integration_report",
]
