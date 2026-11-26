use std::fs::File;
use std::io::{self, Read, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use std::thread::{self, JoinHandle};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const CACHE: &str = "no-store, no-cache, must-revalidate, max-age=0";
const CLEAR_BODY: &str = "<!doctype html><meta charset=\"utf-8\"><title>Clearing preview cache...</title>\n<script>\n(async function() {\n  try { localStorage.clear(); } catch (e) {}\n  try { sessionStorage.clear(); } catch (e) {}\n  try {\n    if ('caches' in window) {\n      const names = await caches.keys();\n      await Promise.all(names.map(n => caches.delete(n)));\n    }\n  } catch (e) {}\n  try {\n    if ('serviceWorker' in navigator) {\n      const regs = await navigator.serviceWorker.getRegistrations();\n      await Promise.all(regs.map(r => r.unregister()));\n    }\n  } catch (e) {}\n  location.replace('/?cb={stamp}&preview={stamp}');\n})();\n</script>\n<p>Clearing preview cache...</p>";

pub struct PreviewServer {
    port: u16,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}
impl PreviewServer {
    pub fn start(folder: &Path) -> io::Result<Self> {
        Self::start_on(folder, 0)
    }

    /// `--serve-port`: bind 127.0.0.1:`port` (0 = fresh random port).
    pub fn start_on(folder: &Path, port: u16) -> io::Result<Self> {
        Self::start_with_tools(folder, port, None)
    }

    /// Like `start_on`, and with `tools` (an HTML snippet) injected before
    /// `</body>` of every served HTML page unless the URL has `?no_tools=1`
    /// (also `tools=0`, `serve_tools=0`, `cyoa_tools=0`). Enables the
    /// `/__serve_tools__` page and `/__serve_tools__/status.json`.
    pub fn start_with_tools(folder: &Path, port: u16, tools: Option<String>) -> io::Result<Self> {
        let root = folder.canonicalize()?;
        let tools: Option<Arc<str>> = tools.map(Arc::from);
        let listener = TcpListener::bind(("127.0.0.1", port))?;
        listener.set_nonblocking(true)?;
        let port = listener.local_addr()?.port();
        let stop = Arc::new(AtomicBool::new(false));
        let flag = Arc::clone(&stop);
        let handle = thread::Builder::new()
            .name("cyoa-preview".into())
            .spawn(move || accept_loop(listener, root, flag, port, tools))?;
        Ok(Self {
            port,
            stop,
            thread: Some(handle),
        })
    }
    pub fn port(&self) -> u16 {
        self.port
    }
    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}/", self.port)
    }
    pub fn stop(mut self) {
        self.shutdown();
    }
    fn shutdown(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(handle) = self.thread.take() {
            let _ = handle.join();
        }
    }
}
impl Drop for PreviewServer {
    fn drop(&mut self) {
        self.shutdown();
    }
}

/// Python `runtime/archive_preview.py::select_archive_root`: the viewer
/// folder inside a CLI output directory (the folder itself when it has
/// `index.html` or `archive_manifest.json`, else its single contained,
/// non-symlink child holding both).
pub fn select_archive_root(output_dir: &Path) -> PathBuf {
    let root = output_dir
        .canonicalize()
        .unwrap_or_else(|_| output_dir.to_path_buf());
    if root.join("index.html").is_file() || root.join("archive_manifest.json").is_file() {
        return root;
    }
    let Ok(entries) = std::fs::read_dir(&root) else {
        return root;
    };
    let mut candidates = Vec::new();
    for entry in entries.flatten() {
        let child = entry.path();
        let is_link = std::fs::symlink_metadata(&child).is_ok_and(|m| m.file_type().is_symlink());
        let Ok(resolved) = child.canonicalize() else {
            continue;
        };
        if !is_link
            && resolved.starts_with(&root)
            && resolved.is_dir()
            && resolved.join("index.html").is_file()
            && resolved.join("archive_manifest.json").is_file()
        {
            candidates.push(resolved);
        }
    }
    if candidates.len() == 1 {
        candidates.remove(0)
    } else {
        root
    }
}

fn accept_loop(
    listener: TcpListener,
    root: PathBuf,
    stop: Arc<AtomicBool>,
    port: u16,
    tools: Option<Arc<str>>,
) {
    let slots = Arc::new(std::sync::Mutex::new(0usize));
    while !stop.load(Ordering::Acquire) {
        match listener.accept() {
            Ok((stream, _)) => {
                let mut count = slots.lock().unwrap_or_else(|e| e.into_inner());
                if *count >= 32 {
                    drop(count);
                    let _ = respond(
                        stream,
                        503,
                        "Service Unavailable",
                        "text/plain",
                        b"",
                        false,
                        &[],
                    );
                    continue;
                }
                *count += 1;
                drop(count);
                let root = root.clone();
                let tools = tools.clone();
                let slots2 = Arc::clone(&slots);
                let _ = thread::Builder::new()
                    .name("cyoa-preview-client".into())
                    .spawn(move || {
                        let _guard = SlotGuard(slots2);
                        if let Err(e) = handle(stream, &root, port, tools.as_deref()) {
                            tracing::debug!("preview request failed: {e}");
                        }
                    });
            }
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(10))
            }
            Err(e) => {
                tracing::debug!("preview accept failed: {e}");
                thread::sleep(Duration::from_millis(20));
            }
        }
    }
}
struct SlotGuard(Arc<std::sync::Mutex<usize>>);
impl Drop for SlotGuard {
    fn drop(&mut self) {
        let mut n = self.0.lock().unwrap_or_else(|e| e.into_inner());
        *n = n.saturating_sub(1);
    }
}

/// Absolute time allowed for the request head (slowloris guard).
const HEADER_DEADLINE: Duration = Duration::from_secs(10);
/// Per-write timeout for response bodies (stalled readers free their slot).
const WRITE_TIMEOUT: Duration = Duration::from_secs(30);

fn handle(mut stream: TcpStream, root: &Path, port: u16, tools: Option<&str>) -> io::Result<()> {
    // Accepted sockets inherit the listener's non-blocking mode on Windows.
    stream.set_nonblocking(false)?;
    stream.set_write_timeout(Some(WRITE_TIMEOUT))?;
    let deadline = std::time::Instant::now() + HEADER_DEADLINE;
    let mut request = Vec::new();
    let mut buf = [0u8; 1024];
    loop {
        if request.len() >= 16 * 1024 {
            return respond(stream, 400, "Bad Request", "text/plain", b"", false, &[]);
        }
        let remaining = deadline.saturating_duration_since(std::time::Instant::now());
        if remaining.is_zero() {
            return respond(stream, 400, "Bad Request", "text/plain", b"", false, &[]);
        }
        stream.set_read_timeout(Some(remaining))?;
        let cap = (16 * 1024 - request.len()).min(buf.len());
        let n = match stream.read(&mut buf[..cap]) {
            Ok(n) => n,
            Err(e)
                if e.kind() == io::ErrorKind::WouldBlock || e.kind() == io::ErrorKind::TimedOut =>
            {
                return respond(stream, 400, "Bad Request", "text/plain", b"", false, &[]);
            }
            Err(e) => return Err(e),
        };
        if n == 0 {
            return respond(stream, 400, "Bad Request", "text/plain", b"", false, &[]);
        }
        request.extend_from_slice(&buf[..n]);
        if request.windows(4).any(|w| w == b"\r\n\r\n") {
            break;
        }
    }
    let end = request
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .unwrap_or(request.len());
    let text = match std::str::from_utf8(&request[..end]) {
        Ok(s) => s,
        Err(_) => return respond(stream, 400, "Bad Request", "text/plain", b"", false, &[]),
    };
    let mut lines = text.split("\r\n");
    let first = match lines.next() {
        Some(s) => s,
        None => return respond(stream, 400, "Bad Request", "text/plain", b"", false, &[]),
    };
    // DNS-rebinding guard (security divergence from Python): the request
    // authority must be this server, given exactly once.
    let hosts: Vec<&str> = lines
        .clone()
        .filter_map(|l| l.split_once(':'))
        .filter(|(k, _)| k.trim().eq_ignore_ascii_case("host"))
        .map(|(_, v)| v.trim())
        .collect();
    let allowed = [format!("127.0.0.1:{port}"), format!("localhost:{port}")];
    if hosts.len() != 1 || !allowed.iter().any(|a| a.eq_ignore_ascii_case(hosts[0])) {
        return respond(stream, 400, "Bad Request", "text/plain", b"", false, &[]);
    }
    let mut pieces = first.split_whitespace();
    let method = pieces.next().unwrap_or("");
    let target = pieces.next().unwrap_or("");
    let version = pieces.next().unwrap_or("");
    if pieces.next().is_some() || !matches!(version, "HTTP/1.0" | "HTTP/1.1") || target.is_empty() {
        return respond(stream, 400, "Bad Request", "text/plain", b"", false, &[]);
    }
    if method == "OPTIONS" {
        return respond(
            stream,
            204,
            "No Content",
            "text/plain",
            b"",
            false,
            &[
                ("Access-Control-Allow-Methods", "GET, OPTIONS"),
                ("Access-Control-Allow-Headers", "*"),
            ],
        );
    }
    if method != "GET" && method != "HEAD" {
        return respond(
            stream,
            405,
            "Method Not Allowed",
            "text/plain",
            b"",
            false,
            &[("Allow", "GET, HEAD, OPTIONS")],
        );
    }
    let head = method == "HEAD";
    let raw_path = target.split(['?', '#']).next().unwrap_or("");
    let decoded = match percent_decode(raw_path) {
        Ok(s) => s,
        Err(()) => return respond(stream, 400, "Bad Request", "text/plain", b"", head, &[]),
    };
    // Control characters (header injection via the redirect) and drive or
    // stream syntax (`C:`, `a:b`) are never valid in a preview path.
    if decoded.chars().any(|c| c.is_control() || c == ':')
        || decoded.contains('\\')
        || decoded.split('/').any(|s| s == "." || s == "..")
    {
        return respond(stream, 400, "Bad Request", "text/plain", b"", head, &[]);
    }
    if decoded == "/__clear_cache__" {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis()
            .to_string();
        let body = CLEAR_BODY.replace("{stamp}", &stamp);
        return respond(
            stream,
            200,
            "OK",
            "text/html; charset=utf-8",
            body.as_bytes(),
            head,
            &[("Clear-Site-Data", "\"cache\", \"storage\"")],
        );
    }
    let query = target.split('#').next().unwrap_or("");
    let query = query.split_once('?').map_or("", |(_, q)| q);
    let tools = tools.filter(|_| !tools_disabled(query));
    if let Some(snippet) = tools {
        if decoded == "/__serve_tools__" || decoded == "/__serve_tools__/" {
            return respond(
                stream,
                200,
                "OK",
                "text/html; charset=utf-8",
                SERVE_TOOLS_PAGE.as_bytes(),
                head,
                &[],
            );
        }
        if decoded == "/__serve_tools__/status.json" {
            let body = format!(
                "{{\n  \"ok\": true,\n  \"server\": \"cyoa-preview\",\n  \"injection\": \"html-intercept\",\n  \"tools_bytes\": {}\n}}\n",
                snippet.len()
            );
            return respond(
                stream,
                200,
                "OK",
                "application/json; charset=utf-8",
                body.as_bytes(),
                head,
                &[],
            );
        }
    }
    let trailing = decoded.ends_with('/');
    let mut path = root.to_path_buf();
    for segment in decoded.split('/').filter(|s| !s.is_empty()) {
        path.push(segment);
    }
    let metadata = match std::fs::metadata(&path) {
        Ok(m) => m,
        Err(_) => return not_found(stream, head),
    };
    if metadata.is_dir() {
        if !trailing {
            // Python SimpleHTTPRequestHandler: the raw (still encoded) path.
            let query = target.find('?').map(|i| &target[i..]).unwrap_or("");
            let location = format!("{raw_path}/{query}");
            return respond(
                stream,
                301,
                "Moved Permanently",
                "text/html",
                b"",
                head,
                &[("Location", location.as_str())],
            );
        }
        path.push("index.html");
        if !path.is_file() {
            path.set_file_name("index.htm");
        }
    }
    let canonical = match path.canonicalize() {
        Ok(p) if p.starts_with(root) => p,
        _ => return not_found(stream, head),
    };
    // Check the type before opening: opening a FIFO blocks until a writer appears.
    if !std::fs::metadata(&canonical).is_ok_and(|m| m.is_file()) {
        return not_found(stream, head);
    }
    let mut file = match File::open(&canonical) {
        Ok(f) => f,
        Err(_) => return not_found(stream, head),
    };
    let len = match file.metadata() {
        Ok(m) if m.is_file() => m.len(),
        _ => return not_found(stream, head),
    };
    let mime = content_type(&canonical);
    // Serve Tools: inject the snippet into HTML pages (bounded size, UTF-8).
    if let Some(snippet) = tools
        && mime.starts_with("text/html")
        && len <= 32 * 1024 * 1024
    {
        let mut raw = Vec::new();
        if file.read_to_end(&mut raw).is_ok() {
            let body = match String::from_utf8(raw) {
                Ok(text) => inject_tools(&text, snippet).into_bytes(),
                Err(e) => e.into_bytes(),
            };
            return respond(stream, 200, "OK", mime, &body, head, &[]);
        }
        return not_found(stream, head);
    }
    write_headers(&mut stream, 200, "OK", mime, len, &[])?;
    if !head {
        let mut chunk = [0u8; 64 * 1024];
        loop {
            match file.read(&mut chunk) {
                Ok(0) => break,
                Ok(n) => {
                    if stream.write_all(&chunk[..n]).is_err() {
                        break;
                    }
                }
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(e) => {
                    tracing::debug!("preview file read failed: {e}");
                    break;
                }
            }
        }
    }
    let _ = stream.shutdown(Shutdown::Both);
    Ok(())
}
fn percent_decode(s: &str) -> Result<String, ()> {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            if i + 2 >= bytes.len() {
                return Err(());
            }
            let hi = hex(bytes[i + 1]).ok_or(())?;
            let lo = hex(bytes[i + 2]).ok_or(())?;
            out.push(hi * 16 + lo);
            i += 3;
        } else {
            out.push(bytes[i]);
            i += 1;
        }
    }
    String::from_utf8(out).map_err(|_| ())
}
fn hex(c: u8) -> Option<u8> {
    match c {
        b'0'..=b'9' => Some(c - b'0'),
        b'a'..=b'f' => Some(c - b'a' + 10),
        b'A'..=b'F' => Some(c - b'A' + 10),
        _ => None,
    }
}
fn content_type(path: &Path) -> &'static str {
    match path
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "html" | "htm" => "text/html",
        "css" => "text/css",
        "js" | "mjs" => "text/javascript",
        "json" => "application/json",
        "txt" => "text/plain",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "svg" => "image/svg+xml",
        "ico" => "image/vnd.microsoft.icon",
        "avif" => "image/avif",
        "bmp" => "image/bmp",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        "ttf" => "font/ttf",
        "otf" => "font/otf",
        "mp3" => "audio/mpeg",
        "ogg" => "audio/ogg",
        "wav" => "audio/x-wav",
        "m4a" => "audio/mp4",
        "mp4" => "video/mp4",
        "webm" => "video/webm",
        "wasm" => "application/wasm",
        "xml" => "text/xml",
        "zip" => "application/zip",
        _ => "application/octet-stream",
    }
}
/// `?no_tools=1`, `tools=0`, `serve_tools=0`, `cyoa_tools=0`: clean preview.
fn tools_disabled(query: &str) -> bool {
    query.split('&').any(|kv| {
        matches!(
            kv,
            "no_tools=1" | "tools=0" | "serve_tools=0" | "cyoa_tools=0"
        )
    })
}

/// Insert `snippet` before the last `</body>` (case-insensitive), else
/// append it; pages that already carry the tools are left alone.
pub fn inject_tools(html: &str, snippet: &str) -> String {
    if html.contains("id=\"cyoa-serve-tools\"") {
        return html.to_owned();
    }
    match html.to_ascii_lowercase().rfind("</body>") {
        Some(i) => format!("{}{snippet}{}", &html[..i], &html[i..]),
        None => format!("{html}{snippet}"),
    }
}

/// `/__serve_tools__`: help page for the injected preview tools.
const SERVE_TOOLS_PAGE: &str = r#"<!doctype html><meta charset="utf-8"><title>CYOA Serve Tools</title>
<style>body{font:15px/1.55 system-ui,Segoe UI,Arial,sans-serif;background:#0f172a;color:#e5e7eb;margin:0}main{max-width:860px;margin:auto;padding:28px}.card{background:#111827;border:1px solid #374151;border-radius:14px;padding:18px;margin:14px 0}a{display:inline-block;margin:5px 6px 5px 0;padding:9px 12px;border-radius:9px;background:#2563eb;color:#fff;text-decoration:none}.muted{color:#9ca3af}code{background:#1f2937;padding:2px 5px;border-radius:5px}</style>
<main><h1>CYOA Serve Tools</h1>
<p class="muted">Local preview helpers. They are injected only into pages served by this preview; downloaded files are never modified.</p>
<div class="card"><h2>Open preview</h2><a href="/?cb=tools">With tools</a><a href="/?no_tools=1">Clean preview</a><a href="/__clear_cache__">Clear state and reopen</a></div>
<div class="card"><h2>What the tools do</h2><ul>
<li><b>⚙ button</b> (bottom right): edit point values, select/deselect choices, unblock audio.</li>
<li><b>Clear state</b>: wipes localStorage, sessionStorage, caches and service workers, then reloads.</li>
<li><b>Reveal UI</b>: re-enables buttons and choices the page greyed out.</li>
<li>Console: <code>window.$serveTools</code> exposes the same helpers.</li></ul></div>
<div class="card"><h2>Disable</h2><p>Add <code>?no_tools=1</code> to any preview URL, or turn off <i>Cheat panel</i> in Settings to drop the ⚙ panel.</p></div>
</main>"#;

fn not_found(stream: TcpStream, head: bool) -> io::Result<()> {
    respond(stream, 404, "Not Found", "text/plain", b"", head, &[])
}
fn respond(
    mut stream: TcpStream,
    status: u16,
    reason: &str,
    mime: &str,
    body: &[u8],
    head: bool,
    extra: &[(&str, &str)],
) -> io::Result<()> {
    write_headers(&mut stream, status, reason, mime, body.len() as u64, extra)?;
    if !head && !body.is_empty() {
        let _ = stream.write_all(body);
    }
    let _ = stream.shutdown(Shutdown::Both);
    Ok(())
}
fn write_headers(
    stream: &mut TcpStream,
    status: u16,
    reason: &str,
    mime: &str,
    len: u64,
    extra: &[(&str, &str)],
) -> io::Result<()> {
    write!(
        stream,
        "HTTP/1.1 {status} {reason}\r\nContent-Type: {mime}\r\nContent-Length: {len}\r\nCache-Control: {CACHE}\r\nPragma: no-cache\r\nExpires: 0\r\nConnection: close\r\nX-Content-Type-Options: nosniff\r\n"
    )?;
    for (name, value) in extra {
        write!(stream, "{name}: {value}\r\n")?;
    }
    stream.write_all(b"\r\n")
}

#[cfg(test)]
#[allow(clippy::expect_used)]
mod tests {
    use super::*;
    fn get(server: &PreviewServer, request: &str) -> (String, Vec<u8>) {
        let mut s = TcpStream::connect(("127.0.0.1", server.port())).expect("connect");
        // Tests write bare request lines; add the valid authority.
        let request = if request.contains("Host:") {
            request.to_owned()
        } else {
            request.replacen(
                "\r\n",
                &format!("\r\nHost: 127.0.0.1:{}\r\n", server.port()),
                1,
            )
        };
        s.write_all(request.as_bytes()).expect("request");
        s.set_read_timeout(Some(Duration::from_secs(2)))
            .expect("timeout");
        let mut b = Vec::new();
        s.read_to_end(&mut b).expect("response");
        let split = b
            .windows(4)
            .position(|w| w == b"\r\n\r\n")
            .unwrap_or(b.len());
        (
            String::from_utf8_lossy(&b[..split]).into_owned(),
            b.get((split + 4).min(b.len())..).unwrap_or(&[]).to_vec(),
        )
    }
    fn server() -> (tempfile::TempDir, PreviewServer) {
        let d = tempfile::tempdir().expect("tempdir");
        let server = PreviewServer::start(d.path()).expect("start");
        (d, server)
    }
    #[test]
    fn serve_tools_are_injected_into_html_only_and_can_be_disabled() {
        let d = tempfile::tempdir().expect("tempdir");
        std::fs::write(
            d.path().join("index.html"),
            "<html><body>home</body></html>",
        )
        .expect("write");
        std::fs::write(d.path().join("a.js"), "x</body>").expect("write");
        let snippet = "<div id=\"cyoa-serve-tools\">T</div>".to_owned();
        let s = PreviewServer::start_with_tools(d.path(), 0, Some(snippet)).expect("start");
        let (h, b) = get(&s, "GET / HTTP/1.1\r\n\r\n");
        assert!(h.contains("200 OK"));
        assert_eq!(
            String::from_utf8_lossy(&b),
            "<html><body>home<div id=\"cyoa-serve-tools\">T</div></body></html>"
        );
        let (_, b) = get(&s, "GET /?no_tools=1 HTTP/1.1\r\n\r\n");
        assert_eq!(
            String::from_utf8_lossy(&b),
            "<html><body>home</body></html>"
        );
        let (_, b) = get(&s, "GET /a.js HTTP/1.1\r\n\r\n");
        assert_eq!(b, b"x</body>");
        let (h, b) = get(&s, "GET /__serve_tools__ HTTP/1.1\r\n\r\n");
        assert!(h.contains("200 OK") && String::from_utf8_lossy(&b).contains("CYOA Serve Tools"));
        let (h, b) = get(&s, "GET /__serve_tools__/status.json HTTP/1.1\r\n\r\n");
        assert!(
            h.contains("application/json") && String::from_utf8_lossy(&b).contains("\"ok\": true")
        );
        // Without tools the routes stay 404 and HTML is untouched.
        let (_d2, plain) = server();
        let (h, _) = get(&plain, "GET /__serve_tools__ HTTP/1.1\r\n\r\n");
        assert!(h.contains("404"));
    }

    #[test]
    fn serves_index_headers_and_nested_content_type() {
        let (d, s) = server();
        std::fs::write(d.path().join("index.html"), "home").expect("write");
        std::fs::create_dir(d.path().join("sub")).expect("mkdir");
        std::fs::write(d.path().join("sub/a.JSON"), "{}").expect("write");
        let (h, b) = get(&s, "GET / HTTP/1.1\r\n\r\n");
        assert!(
            h.contains("200 OK")
                && h.contains("Cache-Control: no-store")
                && !h.contains("Access-Control-Allow-Origin")
        );
        assert_eq!(b, b"home");
        let (h, b) = get(&s, "GET /sub/a.JSON HTTP/1.1\r\n\r\n");
        assert!(h.contains("application/json"));
        assert_eq!(b, b"{}");
    }
    #[test]
    fn rejects_traversal_and_no_listing_redirects() {
        let (d, s) = server();
        for p in ["/../x", "/%2e%2e/x"] {
            let (h, _) = get(&s, &format!("GET {p} HTTP/1.1\r\n\r\n"));
            assert!(h.contains("400 Bad Request"));
        }
        std::fs::create_dir(d.path().join("empty")).expect("mkdir");
        let (h, _) = get(&s, "GET /empty/ HTTP/1.1\r\n\r\n");
        assert!(h.contains("404 Not Found"));
        let (h, _) = get(&s, "GET /empty?x=1 HTTP/1.1\r\n\r\n");
        assert!(h.contains("301 Moved") && h.contains("Location: /empty/?x=1"));
        // Header injection / drive syntax / encoded backslash.
        for p in ["/empty%0d%0aX-Evil:%201", "/C:/Windows", "/a%5c..%5cb"] {
            let (h, _) = get(&s, &format!("GET {p} HTTP/1.1\r\n\r\n"));
            assert!(h.contains("400 Bad Request"), "{p}: {h}");
            assert!(!h.contains("X-Evil"));
        }
    }

    #[test]
    fn rejects_foreign_missing_and_duplicate_host() {
        let (d, s) = server();
        std::fs::write(d.path().join("index.html"), "x").expect("write");
        let p = s.port();
        for req in [
            "GET / HTTP/1.1\r\nHost: evil.example\r\n\r\n".to_owned(),
            format!("GET / HTTP/1.1\r\nHost: evil.example:{p}\r\n\r\n"),
            "GET / HTTP/1.1\r\nHost: 127.0.0.1:1\r\n\r\n".to_owned(),
            format!("GET / HTTP/1.1\r\nHost: 127.0.0.1:{p}\r\nHost: 127.0.0.1:{p}\r\n\r\n"),
            "GET / HTTP/1.0\r\n\r\n".to_owned(),
        ] {
            let mut c = TcpStream::connect(("127.0.0.1", p)).expect("connect");
            c.write_all(req.as_bytes()).expect("write");
            let mut out = Vec::new();
            let _ = c.read_to_end(&mut out);
            assert!(
                String::from_utf8_lossy(&out).starts_with("HTTP/1.1 400"),
                "{req:?}"
            );
        }
        let (h, _) = get(
            &s,
            &format!("GET / HTTP/1.1\r\nHost: LOCALHOST:{p}\r\n\r\n"),
        );
        assert!(h.contains("200 OK"));
    }

    /// Drafted by the offline MiMo worker; reviewed and run by Claude.
    #[test]
    fn windows_device_names_are_not_served() {
        let (_d, s) = server();
        for p in ["/CON", "/nul", "/COM1.txt", "/aux/x", "/LPT1"] {
            let (h, _) = get(&s, &format!("GET {p} HTTP/1.1\r\n\r\n"));
            assert!(!h.contains("200 OK"), "unexpected 200 OK for {p}");
        }
    }

    #[test]
    fn slow_client_is_served() {
        // The request arrives after accept(): the socket must be blocking.
        let (d, s) = server();
        std::fs::write(d.path().join("index.html"), "<p>hi</p>").expect("write");
        let mut c = TcpStream::connect(("127.0.0.1", s.port())).expect("connect");
        thread::sleep(Duration::from_millis(150));
        c.write_all(format!("GET / HTTP/1.1\r\nHost: 127.0.0.1:{}\r\n\r\n", s.port()).as_bytes())
            .expect("write");
        let mut out = Vec::new();
        let _ = c.read_to_end(&mut out);
        let text = String::from_utf8_lossy(&out);
        assert!(text.starts_with("HTTP/1.1 200"), "{text}");
    }
    #[test]
    fn methods_head_and_clear_route() {
        let (_d, s) = server();
        let (h, _) = get(&s, "OPTIONS / HTTP/1.1\r\n\r\n");
        assert!(h.contains("204 No Content") && h.contains("Access-Control-Allow-Headers: *"));
        let (h, _) = get(&s, "POST / HTTP/1.1\r\n\r\n");
        assert!(h.contains("405 Method Not Allowed"));
        let (h, b) = get(&s, "HEAD /__clear_cache__ HTTP/1.1\r\n\r\n");
        assert!(h.contains("Clear-Site-Data:") && h.contains("Content-Length: ") && b.is_empty());
    }
    #[test]
    fn symlink_escape_is_not_served() {
        let (d, s) = server();
        let outside = tempfile::tempdir().expect("outside");
        std::fs::write(outside.path().join("secret.txt"), "secret").expect("write");
        #[cfg(unix)]
        let result = std::os::unix::fs::symlink(
            outside.path().join("secret.txt"),
            d.path().join("escape.txt"),
        );
        #[cfg(windows)]
        let result = std::os::windows::fs::symlink_file(
            outside.path().join("secret.txt"),
            d.path().join("escape.txt"),
        );
        if result.is_err() {
            return;
        }
        let (h, _) = get(&s, "GET /escape.txt HTTP/1.1\r\n\r\n");
        assert!(h.contains("404 Not Found"));
    }
    #[test]
    fn stop_returns_promptly() {
        let (_d, s) = server();
        let start = std::time::Instant::now();
        s.stop();
        assert!(start.elapsed() < Duration::from_secs(1));
    }
}
