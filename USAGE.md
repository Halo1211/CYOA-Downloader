# Usage Guide — CYOA Downloader v1.0 Release

This guide provides practical workflows.

---

## 1. GUI workflow

```bash
python cyoa_downloader.py
```

1. Paste one or more CYOA URLs.
2. Select **ICC Folder** for the first test run.
3. Enable fonts if visual fidelity matters.
4. Keep deep scan enabled unless it causes issues.
5. Click **Preview**.
6. Click **Download All**.
7. Use **Serve** to preview the folder output through localhost.
8. Review reports if any assets are missing.

---

## 2. CLI workflows

### Fast default backup

```bash
python cyoa_downloader.py "https://example.com/cyoa" -o output
```

### ZIP backup

```bash
python cyoa_downloader.py --zip "https://example.com/cyoa" -o output
```

### Full offline ICC folder

```bash
python cyoa_downloader.py --icc-folder "https://example.com/cyoa" -o output_folder
```

### Full offline ICC ZIP

```bash
python cyoa_downloader.py --icc "https://example.com/cyoa" -o output
```

### Folder output with local preview

```bash
python cyoa_downloader.py --icc-folder "https://example.com/cyoa" -o output_folder --serve
```

### Download with fonts

```bash
python cyoa_downloader.py --icc-folder "https://example.com/cyoa" -o output_folder --fonts
```

### Slow network safe mode style command

```bash
python cyoa_downloader.py --icc-folder "https://example.com/cyoa" -o output_folder --threads 2 --wait-time 120 --no-http2
```

---

## 3. Batch workflows

### TXT batch

`batch.txt`:

```text
https://example.com/cyoa-one/
https://example.com/cyoa-two/ | CYOA Two | icc_folder
https://example.com/cyoa-three/ | CYOA Three | zip
```

Run:

```bash
python cyoa_downloader.py --list batch.txt -o outputs
```

### CSV batch

`batch.csv`:

```csv
url,filename,mode
https://example.com/cyoa-one/,CYOA One,icc_folder
https://example.com/cyoa-two/,CYOA Two,icc
https://example.com/cyoa-three/,CYOA Three,both
```

Run:

```bash
python cyoa_downloader.py --list batch.csv -o outputs
```

---

## 4. CYOAP Vue workflow

Use this when the project uses:

```text
dist/platform.json
dist/nodes/list.json
```

Commands:

```bash
python cyoa_downloader.py --cyoap-vue-website "https://example.com/project/" -o output
python cyoa_downloader.py --cyoap-vue-folder "https://example.com/project/" -o output_folder
```

Auto-probe before standard ICC detection:

```bash
python cyoa_downloader.py --cyoap-vue --icc-folder "https://example.com/project/" -o output_folder
```

---

## 5. Pure Website workflow

Use this for custom sites that do not expose standard CYOA project JSON in the expected way.

```bash
python cyoa_downloader.py --pure-website "https://example.com/custom" -o output
python cyoa_downloader.py --pure-website-folder "https://example.com/custom" -o output_folder
```

---

## 6. Diagnostics workflow

Before opening an issue, run:

```bash
python cyoa_downloader.py --dependency-check
python cyoa_downloader.py --self-test
```

For userscript/helper policy:

```bash
python cyoa_downloader.py --userscript-info
```

Export settings safely:

```bash
python cyoa_downloader.py --export-settings settings.safe.json
```

The export redacts secrets and ignores raw secret import.

---

## 7. Recommended issue report

When reporting a problem, include:

- OS and Python version.
- Command used or GUI mode selected.
- Output mode.
- Whether the URL requires login or Cloudflare.
- `--dependency-check` output.
- `backup_report.txt` if generated.
- Relevant lines from `cyoa_downloader.log` with private URLs/tokens removed if needed.

Do not share private API keys, cookies, or authentication headers.
