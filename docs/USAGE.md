# Usage Guide — CYOA Downloader v1.0 Release

This guide shows practical workflows for normal users, advanced users, and maintainers.

---

## 1. Recommended first run

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py
```

The first command checks installed packages. The second runs internal offline checks. The third opens the GUI.

---

## 2. Best default backup mode

For most ICC projects, use **ICC Folder** first:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output_folder
```

Why folder first?

- easier to inspect missing assets;
- easier to preview locally;
- safer for very large projects;
- easier to attach logs/reports to an issue.

After confirming the folder is correct, use ICC ZIP:

```bash
python cyoa_downloader.py --icc "URL" -o output
```

---

## 3. Normal workflows

### Save embedded JSON

```bash
python cyoa_downloader.py "URL" -o output
```

### Save project and external assets as ZIP

```bash
python cyoa_downloader.py --zip "URL" -o output
```

### Save both embedded and ZIP

```bash
python cyoa_downloader.py --both "URL" -o output
```

### Save full offline ICC viewer ZIP

```bash
python cyoa_downloader.py --icc "URL" -o output
```

### Save full offline ICC viewer folder

```bash
python cyoa_downloader.py --icc-folder "URL" -o output_folder
```

### Save and open local preview

```bash
python cyoa_downloader.py --icc-folder "URL" -o output_folder --serve
```

---

## 4. Missing asset recovery workflow

Try this order:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --threads 2 --wait-time 120
```

If assets are still missing:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --gallery-dl smart
```

If the site is Cloudflare protected:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --cloudflare auto
```

If you run FlareSolverr locally:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --cloudflare flaresolverr \
  --flaresolverr-url http://localhost:8191/v1
```

If normal detection still fails and you accept AI usage:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --ai-provider openai \
  --ai-mode auto_fallback \
  --ai-key "YOUR_KEY"
```

---

## 5. Deep scan workflows

Deep scan is enabled by default:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output
```

Disable only for troubleshooting:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --no-deep-scan
```

Disable headless fallback if browser automation causes problems:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --no-selenium
```

Disable `yt-dlp` if external audio extraction is unwanted:

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --no-ytdlp
```

---

## 6. AI Assist workflows

### Diagnostics only

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --ai-provider ollama \
  --ai-model llama3.1 \
  --ai-mode diagnostics
```

### Auto fallback

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --ai-provider anthropic \
  --ai-mode auto_fallback \
  --ai-key "YOUR_KEY"
```

### Aggressive asset recovery

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --ai-provider openrouter \
  --ai-mode aggressive_recovery \
  --ai-max-calls 3 \
  --ai-max-html-chars 8000 \
  --ai-max-js-chars 14000 \
  --ai-key "YOUR_KEY"
```

---

## 7. Network workflows

### Use manual proxy

```bash
python cyoa_downloader.py --icc-folder "URL" -o output \
  --proxy http://127.0.0.1:7890 \
  --proxy-mode manual
```

### Disable proxy/environment proxy

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --proxy-mode disabled
```

### DNS override

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --dns 1.1.1.1
```

### BebasDNS preset

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --bebasdns unfiltered
```

### Enable HTTP/2

```bash
python cyoa_downloader.py --icc-folder "URL" -o output --http2
```

---

## 8. Batch workflows

### TXT batch

```text
https://example.com/cyoa/
https://example.com/cyoa2/ | MyFilename
https://example.com/cyoa3/ | MyZip | icc
https://example.com/cyoa4/ | MyFolder | icc_folder
```

```bash
python cyoa_downloader.py --list batch.txt -o outputs
```

### CSV batch

```csv
url,filename,mode
https://example.com/cyoa1,Project One,icc
https://example.com/cyoa2,Project Two,icc_folder
https://example.com/cyoa3,Project Three,zip
```

```bash
python cyoa_downloader.py --list batch.csv -o outputs
```

### Google Sheets batch

```bash
python cyoa_downloader.py --list "https://docs.google.com/spreadsheets/d/..." -o outputs
```

---

## 9. CYOAP Vue workflow

```bash
python cyoa_downloader.py --cyoap-vue "URL" -o output
python cyoa_downloader.py --cyoap-vue-website "URL" -o output
python cyoa_downloader.py --cyoap-vue-folder "URL" -o output_folder
```

Use CYOAP Vue mode when the project uses `dist/platform.json` and `dist/nodes/list.json`.

---

## 10. Pure website workflow

```bash
python cyoa_downloader.py --pure-website "URL" -o output
python cyoa_downloader.py --pure-website-folder "URL" -o output_folder
```

Use this when the target is a custom viewer/site and normal ICC project JSON discovery is not the correct first step.

---

## 11. Maintenance workflows

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
python cyoa_downloader.py --userscript-info
python cyoa_downloader.py --export-settings settings.redacted.json
python cyoa_downloader.py --import-settings settings.redacted.json
python cyoa_downloader.py --ai-clear-key --ai-provider openai --ai-key-storage keyring
```

---

## 12. v1.0 migration workflow

Old commands:

```bash
python cyoa_downloader.py --website "URL" -o output
python cyoa_downloader.py -W "URL" -o output
python cyoa_downloader.py --website-folder "URL" -o output_folder
```

New commands:

```bash
python cyoa_downloader.py --icc "URL" -o output
python cyoa_downloader.py --icc "URL" -o output
python cyoa_downloader.py --icc-folder "URL" -o output_folder
```

Batch values `website_zip` and `website_folder` remain supported for old files.
