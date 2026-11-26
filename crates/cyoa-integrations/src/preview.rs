use std::fs::File;
use std::io::{self, Read, Seek, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const CACHE: &str = "no-store, no-cache, must-revalidate, max-age=0";
const INSTANCE_HEADER: &str = "X-CYOA-Preview-Instance";
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
        if !cfg!(any(windows, target_os = "linux", target_os = "macos")) {
            return Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "secure preview file opens are unavailable on this platform",
            ));
        }
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
    let mut clients: Vec<(Arc<TcpStream>, JoinHandle<()>)> = Vec::new();
    let instance: Arc<str> = uuid::Uuid::new_v4().to_string().into();
    while !stop.load(Ordering::Acquire) {
        for (_, worker) in clients.extract_if(.., |(_, worker)| worker.is_finished()) {
            let _ = worker.join();
        }
        match listener.accept() {
            Ok((mut stream, _)) => {
                if let Err(e) = stream.set_nonblocking(true) {
                    tracing::debug!("preview socket setup failed: {e}");
                    continue;
                }
                if clients.len() >= 32 {
                    let _ = write_headers(
                        &mut stream,
                        503,
                        "Service Unavailable",
                        "text/plain",
                        0,
                        &[],
                    );
                    let _ = stream.shutdown(Shutdown::Both);
                    continue;
                }
                let stream = Arc::new(stream);
                let socket = Arc::clone(&stream);
                let stop = Arc::clone(&stop);
                let root = root.clone();
                let tools = tools.clone();
                let instance = Arc::clone(&instance);
                match thread::Builder::new()
                    .name("cyoa-preview-client".into())
                    .spawn(move || {
                        let stream = ClientStream {
                            socket: &socket,
                            stop: &stop,
                        };
                        if let Err(e) = handle(stream, &root, port, tools.as_deref(), &instance) {
                            tracing::debug!("preview request failed: {e}");
                        }
                    }) {
                    Ok(worker) => clients.push((stream, worker)),
                    Err(e) => tracing::debug!("preview worker spawn failed: {e}"),
                }
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
    drop(listener);
    // Shut down every client socket before waiting for any worker to exit.
    for (socket, _) in &clients {
        let _ = socket.shutdown(Shutdown::Both);
    }
    for (_, worker) in clients {
        let _ = worker.join();
    }
}

/// Absolute time allowed for the request head (slowloris guard).
const HEADER_DEADLINE: Duration = Duration::from_secs(10);
/// Per-write timeout for response bodies (stalled readers free their slot).
const WRITE_TIMEOUT: Duration = Duration::from_secs(30);

struct ClientStream<'a> {
    socket: &'a TcpStream,
    stop: &'a AtomicBool,
}

impl ClientStream<'_> {
    // Winsock can leave a blocking read pending after socket shutdown.
    fn retry<T>(
        &self,
        deadline: Instant,
        mut operation: impl FnMut(&TcpStream) -> io::Result<T>,
    ) -> io::Result<T> {
        loop {
            if self.stop.load(Ordering::Acquire) {
                return Err(io::ErrorKind::ConnectionAborted.into());
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(io::ErrorKind::TimedOut.into());
            }
            match operation(self.socket) {
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                    thread::sleep(remaining.min(Duration::from_millis(10)));
                }
                result => return result,
            }
        }
    }
}

impl Write for ClientStream<'_> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.retry(Instant::now() + WRITE_TIMEOUT, |mut socket| {
            socket.write(buf)
        })
    }

    fn flush(&mut self) -> io::Result<()> {
        self.socket.flush()
    }
}

fn handle(
    mut stream: ClientStream<'_>,
    root: &Path,
    port: u16,
    tools: Option<&str>,
    instance: &str,
) -> io::Result<()> {
    let deadline = Instant::now() + HEADER_DEADLINE;
    let mut request = Vec::new();
    let mut buf = [0u8; 1024];
    loop {
        if request.len() >= 16 * 1024 {
            return respond(stream, 400, "Bad Request", "text/plain", b"", false, &[]);
        }
        let cap = (16 * 1024 - request.len()).min(buf.len());
        let n = match stream.retry(deadline, |mut socket| socket.read(&mut buf[..cap])) {
            Ok(n) => n,
            Err(e) if e.kind() == io::ErrorKind::TimedOut => {
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
    for (name, value) in lines.filter_map(|line| line.split_once(':')) {
        let (name, value) = (name.trim(), value.trim());
        // Browsers identify worker registrations and updates regardless of the script URL.
        if (name.eq_ignore_ascii_case("Service-Worker") && value.eq_ignore_ascii_case("script"))
            || (name.eq_ignore_ascii_case("Sec-Fetch-Dest")
                && value.eq_ignore_ascii_case("serviceworker"))
        {
            return respond(stream, 403, "Forbidden", "text/plain", b"", head, &[]);
        }
        if name.eq_ignore_ascii_case(INSTANCE_HEADER) && value != instance {
            return respond(
                stream,
                412,
                "Precondition Failed",
                "text/plain",
                b"",
                head,
                &[],
            );
        }
    }
    let instance_headers = [(INSTANCE_HEADER, instance)];
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
            // A HEAD probe must never wipe saved progress.
            if head {
                &[]
            } else {
                &[("Clear-Site-Data", "\"cache\", \"storage\"")]
            },
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
                &instance_headers,
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
                &instance_headers,
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
            // `//sub` would be a scheme-relative URL to host `sub`.
            let location = format!("/{}/{query}", raw_path.trim_start_matches('/'));
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
    // Python serves the MIME type of the requested name, not a symlink target.
    let mime = content_type(&path);
    let (mut file, _) = match open_preview_file(path, root) {
        Ok(opened) => opened,
        Err(_) => return not_found(stream, head),
    };
    let mut len = match file.metadata() {
        Ok(m) if m.is_file() => m.len(),
        _ => return not_found(stream, head),
    };
    let is_html = mime == "text/html";
    let headers: &[_] = if is_html { &instance_headers } else { &[] };
    // Serve Tools: inject the snippet into HTML pages (bounded size, UTF-8).
    if let Some(snippet) = tools
        && is_html
        && len <= HTML_INJECTION_CAP
    {
        match read_tools_html(&mut file, snippet) {
            Ok(Some(body)) => return respond(stream, 200, "OK", mime, &body, head, headers),
            Ok(None) => {
                file.rewind()?;
                len = file.metadata()?.len();
            }
            Err(_) => return not_found(stream, head),
        }
    }
    write_headers(&mut stream, 200, "OK", mime, len, headers)?;
    if !head {
        let mut file = file.take(len);
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
    let _ = stream.socket.shutdown(Shutdown::Both);
    Ok(())
}

const HTML_INJECTION_CAP: u64 = 32 * 1024 * 1024;

fn open_preview_file(path: PathBuf, root: &Path) -> io::Result<(File, PathBuf)> {
    let canonical = path.canonicalize()?;
    if !canonical.starts_with(root) {
        return Err(io::ErrorKind::NotFound.into());
    }
    open_resolved_file(canonical, root)
}

#[cfg(target_os = "linux")]
fn open_resolved_file(path: PathBuf, root: &Path) -> io::Result<(File, PathBuf)> {
    let file = open_linux_file(&path, root, Path::new("/proc/self/fd"))?;
    Ok((file, path))
}

/// Opens `path` (canonical) for reading, confirming it is a regular file that
/// still resolves inside `root`. `fd_dir` is normally `/proc/self/fd`; when it
/// is unavailable the open falls back to a (device, inode) re-check.
#[cfg(target_os = "linux")]
fn open_linux_file(path: &Path, root: &Path, fd_dir: &Path) -> io::Result<File> {
    use std::os::{fd::AsRawFd, unix::fs::OpenOptionsExt};

    #[cfg(any(target_arch = "sparc", target_arch = "sparc64"))]
    const O_PATH: i32 = 0x1000000;
    #[cfg(not(any(target_arch = "sparc", target_arch = "sparc64")))]
    const O_PATH: i32 = 0o10000000;

    // O_PATH opens a reference without opening a device or waiting on a FIFO.
    use std::os::unix::fs::MetadataExt;
    let handle = File::options().read(true).custom_flags(O_PATH).open(path)?;
    let fd_path = fd_dir.join(handle.as_raw_fd().to_string());
    match fd_path.read_link() {
        Ok(opened) => {
            // `opened` may carry " (deleted)"; it is only used for the check.
            if !opened.starts_with(root) || !handle.metadata()?.is_file() {
                return Err(io::ErrorKind::NotFound.into());
            }
            // Reopen the kernel's reference to the same file for reading.
            File::open(fd_path)
        }
        Err(_) => {
            // No procfs (chroot/container): `path` is canonical; open it and
            // confirm (device, inode) that it still resolves inside the root
            // to this very file, so a swap after the check is rejected.
            if !std::fs::symlink_metadata(path)?.is_file() {
                return Err(io::ErrorKind::NotFound.into());
            }
            let file = File::open(path)?;
            let meta = file.metadata()?;
            let again = path.canonicalize()?;
            let check = std::fs::metadata(&again)?;
            if !meta.is_file()
                || !again.starts_with(root)
                || meta.dev() != check.dev()
                || meta.ino() != check.ino()
            {
                return Err(io::ErrorKind::NotFound.into());
            }
            Ok(file)
        }
    }
}

#[cfg(windows)]
fn open_resolved_file(path: PathBuf, _root: &Path) -> io::Result<(File, PathBuf)> {
    use std::os::windows::fs::{MetadataExt, OpenOptionsExt};

    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
    const FILE_TRAVERSE: u32 = 0x20;
    const FILE_READ_ATTRIBUTES: u32 = 0x80;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
    const FILE_SHARE_READ: u32 = 1;
    const FILE_SHARE_WRITE: u32 = 2;

    // Win32's no-follow flag covers the final component. Hold each checked
    // parent open without delete sharing (no rename/replace) until the leaf
    // is open; write sharing stays allowed so a process adding files to the
    // archive does not make every request fail.
    let parents: Vec<_> = path.ancestors().skip(1).collect();
    let mut pinned = Vec::with_capacity(parents.len());
    for parent in parents.into_iter().rev() {
        let dir = File::options()
            .access_mode(FILE_TRAVERSE | FILE_READ_ATTRIBUTES)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS)
            .open(parent)?;
        let meta = dir.metadata()?;
        if !meta.is_dir() || meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(io::ErrorKind::NotFound.into());
        }
        pinned.push(dir);
    }
    let file = File::options()
        .read(true)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(&path)?;
    let meta = file.metadata()?;
    if !meta.is_file() || meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(io::ErrorKind::NotFound.into());
    }
    Ok((file, path))
}

#[cfg(target_os = "macos")]
fn open_resolved_file(path: PathBuf, _root: &Path) -> io::Result<(File, PathBuf)> {
    use std::os::unix::fs::OpenOptionsExt;

    const O_NOFOLLOW_ANY: i32 = 0x2000_0000;
    const O_NONBLOCK: i32 = 4;
    let file = File::options()
        .read(true)
        .custom_flags(O_NOFOLLOW_ANY | O_NONBLOCK)
        .open(&path)?;
    Ok((file, path))
}

#[cfg(not(any(windows, target_os = "linux", target_os = "macos")))]
fn open_resolved_file(_path: PathBuf, _root: &Path) -> io::Result<(File, PathBuf)> {
    Err(io::ErrorKind::Unsupported.into())
}

fn read_tools_html(reader: impl Read, snippet: &str) -> io::Result<Option<Vec<u8>>> {
    let mut raw = Vec::new();
    reader.take(HTML_INJECTION_CAP + 1).read_to_end(&mut raw)?;
    if raw.len() as u64 > HTML_INJECTION_CAP {
        return Ok(None);
    }
    Ok(Some(match String::from_utf8(raw) {
        Ok(text) => inject_tools(&text, snippet).into_bytes(),
        Err(e) => e.into_bytes(),
    }))
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
    match last_body_end(html.as_bytes()) {
        Some(i) => format!("{}{snippet}{}", &html[..i], &html[i..]),
        None => format!("{html}{snippet}"),
    }
}

/// Position of the last real `</body>` tag: one that is not inside a
/// comment, a script (including the script-data escaped states), or an
/// attribute value. Unterminated comments/scripts end the scan.
fn last_body_end(b: &[u8]) -> Option<usize> {
    let lower = b.to_ascii_lowercase();
    let at = |i: usize, s: &str| lower[i..].starts_with(s.as_bytes());
    let delim = |i: usize| {
        lower
            .get(i)
            .is_none_or(|c| c.is_ascii_whitespace() || *c == b'>' || *c == b'/')
    };
    // End of the tag starting at `i` (index after `>`), honouring quotes.
    let tag_end = |mut i: usize| -> Option<usize> {
        let mut quote = None;
        while i < b.len() {
            match (quote, b[i]) {
                (None, b'"' | b'\'') => quote = Some(b[i]),
                (Some(q), c) if c == q => quote = None,
                (None, b'>') => return Some(i + 1),
                _ => {}
            }
            i += 1;
        }
        None
    };
    let mut last = None;
    let mut i = 0;
    while i < b.len() {
        if b[i] != b'<' {
            i += 1;
            continue;
        }
        if at(i, "<!--") {
            let Some(end) = find(&lower, i + 4, b"-->") else {
                break;
            };
            i = end + 3;
        } else if at(i, "<script") && delim(i + 7) {
            let Some(mut j) = tag_end(i + 7) else { break };
            // Script data with the escaped / double-escaped states.
            let (mut escaped, mut double) = (false, false);
            let end = loop {
                if j >= b.len() {
                    break None;
                }
                if at(j, "<!--") && !escaped {
                    escaped = true;
                    j += 4;
                } else if escaped && at(j, "-->") {
                    escaped = false;
                    double = false;
                    j += 3;
                } else if escaped && !double && at(j, "<script") && delim(j + 7) {
                    double = true;
                    j += 7;
                } else if at(j, "</script") && delim(j + 8) {
                    if double {
                        double = false;
                        j += 8;
                    } else {
                        break tag_end(j + 8);
                    }
                } else {
                    j += 1;
                }
            };
            let Some(end) = end else { break };
            i = end;
        } else if at(i, "</body") && delim(i + 6) {
            last = Some(i);
            let Some(end) = tag_end(i + 6) else { break };
            i = end;
        } else if b
            .get(i + 1)
            .is_some_and(|c| c.is_ascii_alphabetic() || *c == b'/' || *c == b'!')
        {
            let Some(end) = tag_end(i + 1) else { break };
            i = end;
        } else {
            i += 1;
        }
    }
    last
}

fn find(hay: &[u8], from: usize, needle: &[u8]) -> Option<usize> {
    hay.get(from..)?
        .windows(needle.len())
        .position(|w| w == needle)
        .map(|p| p + from)
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

fn not_found(stream: ClientStream<'_>, head: bool) -> io::Result<()> {
    respond(stream, 404, "Not Found", "text/plain", b"", head, &[])
}
fn respond(
    mut stream: ClientStream<'_>,
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
    let _ = stream.socket.shutdown(Shutdown::Both);
    Ok(())
}
fn write_headers(
    stream: &mut impl Write,
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

    #[test]
    fn tools_not_injected_inside_script_string() {
        let html = "<p>x</p><script>window.ready = \"</body>\";</script>";
        let out = inject_tools(html, "<!--T-->");
        assert_eq!(out, format!("{html}<!--T-->"));
        let normal = "<script>a()</script></body>";
        assert_eq!(
            inject_tools(normal, "<!--T-->"),
            "<script>a()</script><!--T--></body>"
        );
    }

    #[test]
    fn tools_use_last_body_end_outside_scripts_and_comments() {
        for tail in [
            "<!-- truncated </script>",
            "<!-- </body> -->",
            r#"<ScRiPt data-note=">">const fake = "</body>"; </scripture>const stillFake = "</body>";</sCrIpT >"#,
            "<script><!-- <script></script></body> --></script>",
            r#"<div title="</body>"></div>"#,
        ] {
            let html = format!("<body>First</body><body>Game</BoDy></html>{tail}");
            assert_eq!(
                inject_tools(&html, "<!--T-->"),
                format!("<body>First</body><body>Game<!--T--></BoDy></html>{tail}")
            );
        }
    }

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

    #[cfg(windows)]
    #[test]
    fn serves_files_while_directory_has_shared_write_handle() {
        use std::os::windows::fs::OpenOptionsExt;

        let (d, s) = server();
        std::fs::write(d.path().join("index.html"), "home").expect("write");
        let _writer = File::options()
            .access_mode(0x0002) // FILE_ADD_FILE
            .share_mode(0x0001 | 0x0002 | 0x0004) // FILE_SHARE_READ | WRITE | DELETE
            .custom_flags(0x0200_0000) // FILE_FLAG_BACKUP_SEMANTICS
            .open(d.path())
            .expect("open shared directory writer");

        let (headers, body) = get(&s, "GET /index.html HTTP/1.1\r\n\r\n");
        assert!(headers.contains("200 OK"), "{headers}");
        assert_eq!(body, b"home");
    }

    #[cfg(unix)]
    #[test]
    fn serves_mime_type_of_requested_name() {
        let (d, s) = server();
        std::fs::write(d.path().join("script.data"), "void 0;").expect("write");
        std::os::unix::fs::symlink("script.data", d.path().join("app.js")).expect("symlink");

        let (headers, body) = get(&s, "GET /app.js HTTP/1.1\r\n\r\n");
        assert!(
            headers.contains("Content-Type: text/javascript\r\n"),
            "{headers}"
        );
        assert_eq!(body, b"void 0;");
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn opens_file_without_procfs_and_rejects_replaced_parent() {
        let d = tempfile::tempdir().expect("tempdir");
        let root = d.path().join("archive");
        let parent = root.join("pages");
        let outside = d.path().join("outside");
        std::fs::create_dir_all(&parent).expect("pages");
        std::fs::create_dir(&outside).expect("outside");
        std::fs::write(parent.join("index.html"), "home").expect("write page");
        std::fs::write(outside.join("index.html"), "outside").expect("write outside page");
        let root = root.canonicalize().expect("root");
        let path = root.join("pages/index.html");
        let missing_fds = d.path().join("missing-proc/self/fd");

        let mut file = open_linux_file(&path, &root, &missing_fds).expect("open without procfs");
        let mut body = String::new();
        file.read_to_string(&mut body).expect("read page");
        assert_eq!(body, "home");

        std::fs::rename(&parent, root.join("old-pages")).expect("rename pages");
        std::os::unix::fs::symlink(&outside, &parent).expect("replace pages with symlink");
        assert!(open_linux_file(&path, &root, &missing_fds).is_err());
    }

    #[test]
    fn html_injection_reads_at_most_cap_plus_one() {
        let mut reader = io::repeat(b'x').take(HTML_INJECTION_CAP + 10);
        assert!(
            read_tools_html(&mut reader, "<!-- tools -->")
                .expect("read")
                .is_none()
        );
        assert_eq!(reader.limit(), 9);
    }

    #[cfg(any(windows, target_os = "linux", target_os = "macos"))]
    #[test]
    fn replaced_parent_is_rejected_at_open() {
        let fixture = tempfile::tempdir().expect("tempdir");
        let root = fixture.path().join("archive");
        let parent = root.join("pages");
        let other = fixture.path().join("outside");
        std::fs::create_dir_all(&parent).expect("parent");
        std::fs::create_dir(&other).expect("outside");
        std::fs::write(parent.join("page.html"), "archive").expect("write");
        std::fs::write(other.join("page.html"), "outside").expect("write");
        let root = root.canonicalize().expect("root");
        let path = parent.join("page.html").canonicalize().expect("page");
        let _ = open_resolved_file(path.clone(), &root).expect("open ordinary archive page");

        std::fs::remove_file(parent.join("page.html")).expect("remove fixture page");
        std::fs::remove_dir(&parent).expect("remove fixture directory");
        #[cfg(unix)]
        std::os::unix::fs::symlink(&other, &parent).expect("symlink");
        #[cfg(windows)]
        {
            // Creating a junction works without Windows Developer Mode.
            let output = std::process::Command::new("powershell.exe")
                .args([
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "New-Item -ItemType Junction -Path $env:CYOA_TEST_LINK -Target $env:CYOA_TEST_TARGET -ErrorAction Stop | Out-Null",
                ])
                .env("CYOA_TEST_LINK", &parent)
                .env("CYOA_TEST_TARGET", &other)
                .output()
                .expect("create junction");
            assert!(output.status.success(), "{:?}", output);
        }
        assert!(open_resolved_file(path, &root).is_err());
    }

    #[test]
    fn rejects_service_worker_script_requests() {
        let (d, s) = server();
        std::fs::write(d.path().join("worker.js"), "self.skipWaiting()").expect("write");
        for header in ["Service-Worker: script", "Sec-Fetch-Dest: serviceworker"] {
            let (h, _) = get(&s, &format!("GET /worker.js HTTP/1.1\r\n{header}\r\n\r\n"));
            assert!(h.contains("403 Forbidden"), "{h}");
        }
        let (h, _) = get(&s, "GET /worker.js HTTP/1.1\r\n\r\n");
        assert!(h.contains("200 OK"));
    }

    #[test]
    fn rejects_instance_header_from_previous_server() {
        let (d, first) = server();
        std::fs::write(d.path().join("index.html"), "first").expect("write");
        let port = first.port();
        let (h, _) = get(&first, "GET / HTTP/1.1\r\n\r\n");
        let first_id = h
            .lines()
            .find_map(|line| line.strip_prefix("X-CYOA-Preview-Instance: "))
            .expect("first instance header");
        first.stop();

        let other = tempfile::tempdir().expect("other archive");
        std::fs::write(other.path().join("index.html"), "second").expect("write");
        let second = PreviewServer::start_on(other.path(), port).expect("reuse port");
        let (h, _) = get(
            &second,
            &format!("GET / HTTP/1.1\r\nX-CYOA-Preview-Instance: {first_id}\r\n\r\n"),
        );
        assert!(h.contains("412 Precondition Failed"), "{h}");

        let (h, body) = get(&second, "GET / HTTP/1.1\r\n\r\n");
        assert!(h.contains("Cache-Control: no-store"));
        let second_id = h
            .lines()
            .find_map(|line| line.strip_prefix("X-CYOA-Preview-Instance: "))
            .expect("second instance header");
        assert_ne!(first_id, second_id);
        assert_eq!(body, b"second");
        let (h, _) = get(
            &second,
            &format!("GET / HTTP/1.1\r\nX-CYOA-Preview-Instance: {second_id}\r\n\r\n"),
        );
        assert!(h.contains("200 OK"));
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
        // The request arrives after accept(): the worker must wait for it.
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
        assert!(!h.contains("Clear-Site-Data:") && h.contains("Content-Length: ") && b.is_empty());
        let (h, _) = get(&s, "GET /__clear_cache__ HTTP/1.1\r\n\r\n");
        assert!(h.contains("Clear-Site-Data:"));
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
    fn stop_closes_accepted_connection_with_incomplete_request() {
        let (_d, s) = server();
        let mut client = TcpStream::connect(("127.0.0.1", s.port())).expect("connect");
        client
            .write_all(format!("GET / HTTP/1.1\r\nHost: 127.0.0.1:{}\r\n", s.port()).as_bytes())
            .expect("partial request");
        // A later request passing through the same listener confirms accept() advanced.
        let (headers, _) = get(&s, "OPTIONS / HTTP/1.1\r\n\r\n");
        assert!(headers.contains("204 No Content"));

        let start = std::time::Instant::now();
        s.stop();
        assert!(start.elapsed() < Duration::from_secs(2));

        client
            .set_read_timeout(Some(Duration::from_secs(2)))
            .expect("read timeout");
        match client.read(&mut [0]) {
            Ok(0) => {}
            Err(e) if e.kind() == io::ErrorKind::ConnectionReset => {}
            result => panic!("expected the accepted connection to close: {result:?}"),
        }
    }

    #[test]
    fn stop_returns_promptly() {
        let (_d, s) = server();
        let start = std::time::Instant::now();
        s.stop();
        assert!(start.elapsed() < Duration::from_secs(1));
    }
}
