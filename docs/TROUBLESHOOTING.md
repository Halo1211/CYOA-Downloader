# Troubleshooting — CYOA Downloader v1.0 Release

This guide lists common problems and recommended fixes.

---

## 1. CLI says `--website` is unknown

v1.0 intentionally removed old Website Mode flags.

Use:

```bash
python cyoa_downloader.py --icc "URL" -o output
python cyoa_downloader.py --icc-folder "URL" -o output_folder
```

Do not use:

```bash
python cyoa_downloader.py --website "URL"
python cyoa_downloader.py -W "URL"
python cyoa_downloader.py --website-folder "URL"
```

Batch values `website_zip` and `website_folder` are still accepted for old batch/settings/manifest compatibility.

---

## 2. Missing images/assets

Recommended recovery order:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --threads 2 --wait-time 120
```

Then inspect:

```text
output/cyoa_downloader.log
output/backup_report.txt
output/failed_assets.txt
```

Try these options if needed:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --fonts
python cyoa_downloader.py --icc-folder "URL" -o output --gallery-dl smart
python cyoa_downloader.py --icc-folder "URL" -o output --cloudflare auto
```

Keep deep scan enabled unless it causes a specific problem.

---

## 3. Site is Cloudflare protected

Try auto first:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --cloudflare auto
```

If cloudscraper is installed:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --cloudflare cloudscraper
```

If using FlareSolverr:

```bash
python cyoa_downloader.py --flaresolverr-test
python cyoa_downloader.py --icc-folder "URL" -o output \
  --cloudflare flaresolverr \
  --flaresolverr-url http://localhost:8191/v1
```

---

## 4. Network is slow or unstable

Use fewer threads and longer waits:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --threads 2 --wait-time 120
```

Disable HTTP/2 if it causes issues:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --no-http2
```

Add bandwidth cap if needed:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --bandwidth 512
```

---

## 5. Proxy or DNS issue

Disable proxy entirely:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --proxy-mode disabled
```

Use manual proxy:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --proxy http://127.0.0.1:7890 \
  --proxy-mode manual
```

Use DNS override:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --dns 1.1.1.1
```

Use BebasDNS preset:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --bebasdns unfiltered
```

---

## 6. Dependency missing

Run:

```bash
python cyoa_downloader.py --dependency-check
```

Install standard requirements:

```bash
pip install -r requirements.txt
```

Install optional recovery dependencies as needed:

```bash
pip install httpx[h2] cloudscraper selenium yt-dlp gallery-dl keyring json5 tldextract
```

For Excel batch import:

```bash
pip install pandas openpyxl xlrd
```

---

## 7. AI Assist does not run

Check these items:

- `--ai-mode` is not `off`;
- provider is supported;
- model name is valid;
- API key exists via `--ai-key`, env, keyring, or plain settings;
- budget is not exhausted;
- for Ollama, local Ollama is running and `--ollama-url` is correct.

Example local diagnostics mode:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --ai-provider ollama \
  --ai-model llama3.1 \
  --ai-mode diagnostics
```

---

## 8. GUI freezes or logs spam

v1.0 includes batched GUI log flushing, but extreme downloads can still produce many lines. Try:

- fewer threads;
- ICC Folder instead of ICC ZIP;
- disable optional extractors that are failing repeatedly;
- check `cyoa_downloader.log` instead of relying only on the GUI panel.

---

## 9. ZIP is corrupted or incomplete

Try folder output first:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output_folder
```

If folder output is correct, rerun ZIP mode:

```bash
python cyoa_downloader.py --icc "URL" -o output
```

Check disk space, antivirus interference, and file permission issues.

---

## 10. Windows path or permission problem

Use a short output path outside OneDrive/Google Drive sync folders:

```powershell
mkdir C:\CYOA_Output
python cyoa_downloader.py --icc-folder "URL" -o C:\CYOA_Output
```

Avoid very long folder names and special characters.

---

## 11. What to include in a GitHub issue

Attach or paste:

- command used;
- OS and Python version;
- `python cyoa_downloader.py --dependency-check` output;
- `python cyoa_downloader.py --self-test` output;
- relevant part of `cyoa_downloader.log`;
- `backup_report.txt` or `failed_assets.txt` if present;
- whether the issue happens in `--icc-folder` mode.

Do not include private API keys, tokens, cookies, or paid/private content URLs.
