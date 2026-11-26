//! Serve Tools: the helper snippet the local preview server injects into
//! served HTML pages (never into downloaded files).
//!
//! Written for this project (it replaces Python's bundled third-party
//! userscript loader): a small corner panel with Clear state / Reveal UI /
//! Clean preview, plus the project's own ⚙ cheat panel when enabled.

use crate::offline_viewer_consts::CHEAT_OVERLAY;

const PANEL: &str = r#"<div id="cyoa-serve-tools" class="min">
<style>
#cyoa-serve-tools{position:fixed;top:10px;right:10px;z-index:2147483646;font:12px/1.4 system-ui,Segoe UI,Arial,sans-serif;color:#f9fafb}
#cyoa-serve-tools .bar{display:flex;gap:6px;align-items:center;background:#111827e6;border:1px solid #374151;border-radius:10px;padding:5px 7px;box-shadow:0 8px 24px #0006}
#cyoa-serve-tools button,#cyoa-serve-tools a{border:0;border-radius:7px;padding:5px 8px;background:#374151;color:#fff;text-decoration:none;cursor:pointer;font:inherit}
#cyoa-serve-tools button:hover,#cyoa-serve-tools a:hover{background:#2563eb}
#cyoa-serve-tools .t{font-weight:700;cursor:pointer;padding:2px 4px}
#cyoa-serve-tools.min .x{display:none}
</style>
<div class="bar"><span class="t" data-a="toggle" title="CYOA Serve Tools">⚡ Tools</span>
<span class="x"><button data-a="clear" title="Wipe storage, caches and service workers, then reload">Clear state</button>
<button data-a="reveal" title="Re-enable greyed-out buttons and choices">Reveal UI</button>
<a data-a="clean" href="?no_tools=1" title="Reload without tools">Clean</a>
<a href="/__serve_tools__" target="_blank" title="Help">?</a></span></div>
<script>(function(){
var root=document.getElementById('cyoa-serve-tools');if(!root||root.dataset.ready)return;root.dataset.ready='1';
var clean=root.querySelector('[data-a="clean"]');
function updateCleanLink(){
 var href=location.href;
 var hash=href.indexOf('#');
 var url=hash<0?href:href.slice(0,hash);
 var query=url.indexOf('?');
 var separator=query<0?'?':query===url.length-1?'':'&';
 clean.href=url+separator+'no_tools=1'+href.slice(url.length);
}
updateCleanLink();
['click','auxclick','contextmenu'].forEach(function(event){clean.addEventListener(event,updateCleanLink);});
async function clearState(){
 try{localStorage.clear();sessionStorage.clear();}catch(e){}
 try{if('caches' in window){var k=await caches.keys();await Promise.all(k.map(function(n){return caches.delete(n);}));}}catch(e){}
 try{if('serviceWorker' in navigator){var r=await navigator.serviceWorker.getRegistrations();await Promise.all(r.map(function(x){return x.unregister();}));}}catch(e){}
 location.replace(location.pathname+'?cb='+Date.now());
}
function revealUI(){
 var n=0;document.querySelectorAll('[disabled],.disabled,.is-disabled,.locked,.unavailable').forEach(function(el){
  if(root.contains(el))return;
  try{el.disabled=false;el.classList.remove('disabled','is-disabled','locked','unavailable');el.style.pointerEvents='auto';el.style.opacity='1';n++;}catch(e){}
 });return n;
}
window.$serveTools={clearState:clearState,revealUI:revealUI};
root.addEventListener('click',function(ev){var a=ev.target&&ev.target.getAttribute&&ev.target.getAttribute('data-a');
 if(a==='toggle')root.classList.toggle('min');
 if(a==='clear'&&confirm('Clear saved progress for this preview and reload?'))clearState();
 if(a==='reveal'){var n=revealUI();ev.target.textContent='Revealed '+n;setTimeout(function(){ev.target.textContent='Reveal UI';},1500);}
});
var tpl=document.getElementById('cyoa-serve-tools-cheat');
if(tpl&&!document.getElementById('__cyoa_gear')){(document.body||document.documentElement).appendChild(document.importNode(tpl.content,true));}
})();</script>
</div>"#;

/// The snippet the preview server injects; `cheat_panel` follows the
/// `cheat_enabled` setting (default on).
pub fn serve_tools_snippet(cheat_panel: bool) -> String {
    let cheat = if cheat_panel {
        format!("<template id=\"cyoa-serve-tools-cheat\">{CHEAT_OVERLAY}</template>")
    } else {
        String::new()
    };
    format!("\n{cheat}{PANEL}\n")
}

/// Serve preview options from settings.json (shared by the CLI and GUI).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ServeOptions {
    /// `serve_enabled` (default on).
    pub enabled: bool,
    /// `serve_port`: 0 = random free port.
    pub port: u16,
    /// `serve_open_browser` (default on).
    pub open_browser: bool,
    /// `serve_tools_enabled` (default on): inject the ⚡ Tools bar.
    pub tools: bool,
    /// `cheat_enabled` (default on): include the ⚙ cheat panel.
    pub cheat: bool,
    /// `serve_clear_on_start` (default off): open via `/__clear_cache__`.
    pub clear_on_start: bool,
    /// `serve_auto_after_download` (GUI, default off).
    pub auto_after_download: bool,
}

impl ServeOptions {
    pub fn from_settings(settings: &serde_json::Map<String, serde_json::Value>) -> Self {
        let flag = |k: &str, d: bool| {
            settings
                .get(k)
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(d)
        };
        Self {
            enabled: flag("serve_enabled", true),
            port: settings
                .get("serve_port")
                .and_then(serde_json::Value::as_u64)
                .and_then(|p| u16::try_from(p).ok())
                .unwrap_or(0),
            open_browser: flag("serve_open_browser", true),
            tools: flag("serve_tools_enabled", true),
            cheat: cheat_enabled(settings),
            clear_on_start: flag("serve_clear_on_start", false),
            auto_after_download: flag("serve_auto_after_download", false),
        }
    }

    /// The snippet to inject, or `None` when Serve Tools are off.
    pub fn snippet(&self) -> Option<String> {
        self.tools.then(|| serve_tools_snippet(self.cheat))
    }

    /// The URL to open for a server at `base` (`http://127.0.0.1:PORT/`).
    pub fn open_url(&self, base: &str) -> String {
        if self.clear_on_start {
            let stamp = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis())
                .unwrap_or_default();
            format!("{base}__clear_cache__?cb={stamp}")
        } else {
            base.to_owned()
        }
    }
}

/// `cheat_enabled` from settings.json values (default on).
pub fn cheat_enabled(settings: &serde_json::Map<String, serde_json::Value>) -> bool {
    settings
        .get("cheat_enabled")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snippet_has_marker_and_optional_cheat_panel() {
        let on = serve_tools_snippet(true);
        assert!(on.contains("id=\"cyoa-serve-tools\""));
        assert!(on.contains("cyoa-serve-tools-cheat"));
        let off = serve_tools_snippet(false);
        assert!(off.contains("id=\"cyoa-serve-tools\""));
        assert!(!off.contains("<template"));
    }

    #[test]
    fn clean_link_adds_flag_to_current_url() -> Result<(), Box<dyn std::error::Error>> {
        check_clean_link("")
    }

    #[test]
    fn clean_link_works_when_page_declares_url() -> Result<(), Box<dyn std::error::Error>> {
        check_clean_link("const URL = './project.json';")
    }

    fn check_clean_link(page_setup: &str) -> Result<(), Box<dyn std::error::Error>> {
        let snippet = serve_tools_snippet(false);
        assert!(snippet.contains(r#"<a data-a="clean" href="?no_tools=1""#));
        let (_, script) = snippet
            .split_once("<script>")
            .ok_or("missing panel script")?;
        let (script, _) = script
            .split_once("</script>")
            .ok_or("unclosed panel script")?;
        let setup = r#"
            const assert = require('node:assert/strict');
            const window = {};
            const location = {href: 'http://127.0.0.1:8080/play/?scenario=a%2Bb#start?x'};
            const events = {};
            const clean = {addEventListener(name, callback) { events[name] = callback; }};
            const root = {dataset: {}, querySelector() { return clean; }, addEventListener() {}};
            const document = {getElementById(id) { return id === 'cyoa-serve-tools' ? root : null; }};
        "#;
        let checks = r#"
            assert.equal(typeof window.$serveTools.revealUI, 'function');
            assert.equal(clean.href, 'http://127.0.0.1:8080/play/?scenario=a%2Bb&no_tools=1#start?x');
            location.href = 'http://127.0.0.1:8080/play/#next';
            for (const event of ['click', 'auxclick', 'contextmenu']) {
                clean.href = '';
                events[event]();
                assert.equal(clean.href, 'http://127.0.0.1:8080/play/?no_tools=1#next');
            }
            location.href = 'http://127.0.0.1:8080/play/?#';
            events.click();
            assert.equal(clean.href, 'http://127.0.0.1:8080/play/?no_tools=1#');
        "#;
        match std::process::Command::new("node")
            .args(["-e", &format!("{setup}{page_setup}{script}{checks}")])
            .output()
        {
            Ok(output) => assert!(
                output.status.success(),
                "{}",
                String::from_utf8_lossy(&output.stderr)
            ),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                eprintln!(
                    "Skipping Serve Tools JavaScript execution because Node.js is unavailable"
                );
            }
            Err(error) => return Err(error.into()),
        }
        Ok(())
    }

    #[test]
    fn serve_options_defaults_and_overrides() {
        let d = ServeOptions::from_settings(&serde_json::Map::new());
        assert!(d.enabled && d.open_browser && d.tools && d.cheat);
        assert!(!d.clear_on_start && !d.auto_after_download);
        assert_eq!(d.port, 0);
        assert!(d.snippet().is_some());
        assert_eq!(d.open_url("http://127.0.0.1:5/"), "http://127.0.0.1:5/");
        let s: serde_json::Map<String, serde_json::Value> = serde_json::from_str(
            r#"{"serve_port": 8123, "serve_tools_enabled": false, "serve_clear_on_start": true}"#,
        )
        .unwrap_or_default();
        let o = ServeOptions::from_settings(&s);
        assert_eq!(o.port, 8123);
        assert!(o.snippet().is_none());
        assert!(
            o.open_url("http://127.0.0.1:5/")
                .starts_with("http://127.0.0.1:5/__clear_cache__?cb=")
        );
    }

    #[test]
    fn injection_goes_before_body_end_once() {
        let html = "<html><body><p>x</p></BODY></html>";
        let out = cyoa_integrations::preview::inject_tools(html, &serve_tools_snippet(false));
        let at = out.find("cyoa-serve-tools").unwrap_or(usize::MAX);
        assert!(at < out.find("</BODY>").unwrap_or(0));
        let again = cyoa_integrations::preview::inject_tools(&out, &serve_tools_snippet(false));
        assert_eq!(again, out);
    }
}
