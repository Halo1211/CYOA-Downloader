# Dependency map: Python → Rust

Portability classes:
- **N** — native Rust crate
- **X** — external executable called with an argument array and bounded output
- **B** — browser automation over the Chrome DevTools Protocol (CDP)
- **D** — defer or drop, which needs owner approval

The crates listed are candidates. Each version and API must be checked against official docs (Context7 or docs.rs) before it is added. **Nothing in this table has been added yet.**

| Python | Used for | Required? | Class | Rust candidate | Risk / parity note |
|---|---|---|---|---|---|
| requests (+socks) | Core HTTP | Required | N | `reqwest` (rustls, stream, socks, cookies) | Redirect policy and header stripping across origins; proxy env handling |
| httpx[http2] | HTTP/2 deep scan | Optional | N | `reqwest` http2 feature | Python buffers bodies without a cap (SEC-4). Add one |
| beautifulsoup4 / soupsieve | HTML parsing and rewriting | Required | N | `scraper` / `html5ever`, or `lol_html` for streaming rewrites | Tree and serialization differ from `html.parser`. Golden-file tests needed |
| json5 | Lenient project.json | Optional | N | `json5` crate | Error positions and edge syntax |
| tldextract | Registrable domain | Optional | N | `psl` / `publicsuffix` | PSL snapshot version |
| Pillow | Image detect/convert/resize | Optional | N | `image` (+ `fast_image_resize`) | Codec coverage, decompression-bomb limits |
| pandas / openpyxl / xlrd | Batch XLSX/XLS import | Optional | N | `csv`, `calamine` (`rust_xlsxwriter` if exporting XLSX) | Cell typing, dates |
| keyring | Secrets | Optional | N | `keyring` (Windows Credential Manager) | Same service and account names for migration |
| dnspython | UDP/TCP/DoH/DoT DNS | Optional | N | `hickory-resolver` | Integration with the reqwest resolver |
| sqlite3 (stdlib) | Checkpoints, CYOA Manager | Required | N | `rusqlite` (bundled) | Same schemas |
| zipfile (stdlib) | Packages, viewers | Required | N | `zip` | ZIP64, the Python validation rules (SEC-5) |
| http.server (stdlib) | Local preview server | Required | N | `hyper`/`axum` or `tiny_http` | Fix SEC-2 during the port |
| hashlib, json, csv, re | Basics | Required | N | `sha2`, `serde_json` (`preserve_order`), `csv`, `regex` (+ `fancy-regex` only where lookarounds are needed) | Regex rewrites |
| customtkinter / tkinter | GUI | Required | N | `eframe`/`egui` + `rfd` dialogs | See ARCHITECTURE.md |
| playwright | Browser render and runtime capture | Optional | B | `chromiumoxide` (CDP) against installed Chrome/Edge | Large surface; must enforce destination policy (SEC-1) |
| selenium | Duplicate browser fallback | Optional | D | — | Keep only Playwright-equivalent CDP |
| cloudscraper | Cloudflare JS challenge | Optional | D/B | Browser session hand-off via CDP | No Rust equivalent; must not bypass access controls |
| browser-cookie3 | Import browser cookies | Optional | D | — | Windows app-bound encryption; sensitive. Defer |
| yt-dlp | YouTube audio | Optional | X | `yt-dlp.exe` (+ `ffmpeg`) | Process-tree cancellation (Job Objects), progress parsing |
| gallery-dl | Gallery fallback | Optional | X | `gallery-dl.exe` | Output containment |
| itch-dl | itch.io | Optional | X | Already a subprocess (`uvx`/`pipx`) | Unchanged |
| rarfile (+ unrar/7z) | RAR viewers | Optional | X | `unrar`/`7z` subprocess | Bounded stdout (SEC-4) |
| plyer | Desktop notifications | Optional | N/D | `notify-rust` or `winrt-notification` | Low priority |
| AI providers (HTTP) | AI helper | Optional | N | `reqwest` | Secrets in the keyring |

Infrastructure: `tokio`, `tokio-util` (CancellationToken), `futures`, `tracing`, `thiserror` (library crates), `anyhow` (binary only), `url`, `tempfile`.
Dev: `wiremock`, `proptest`, `insta`, `criterion`.

**Selenium, browser-cookie3 and cloudscraper would be removed or deferred. That needs owner approval (decision D3).**
