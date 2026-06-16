# Usage

## GUI workflow

1. Run `python cyoa_downloader.py`.
2. Add one or more CYOA URLs to the queue.
3. Choose an output mode.
4. For most offline viewer backups, use **ICC Folder** or **ICC ZIP**.
5. Use Preview before large batch runs.
6. Use Serve to open generated ICC folders through localhost.
7. Review `backup_report.txt`, `failed_assets.txt`, and `cyoa_downloader.log` if assets fail.

## CLI examples

```bash
python cyoa_downloader.py "https://example.com/cyoa" -o output
python cyoa_downloader.py --zip "https://example.com/cyoa" -o output
python cyoa_downloader.py --both "https://example.com/cyoa" -o output
python cyoa_downloader.py --icc "https://example.com/cyoa" -o output.zip
python cyoa_downloader.py --icc-folder "https://example.com/cyoa" -o output_folder
python cyoa_downloader.py --icc-folder "https://example.com/cyoa" -o output_folder --serve
```

## Batch examples

```bash
python cyoa_downloader.py --list batch.txt -o outputs
python cyoa_downloader.py --list batch.csv -o outputs
python cyoa_downloader.py --list batch.xlsx -o outputs
```

Supported batch mode values include `embed`, `zip`, `both`, `website_zip`, `website_folder`, `icc`, `icc_zip`, `icc_folder`, `cyoap_vue_zip`, `cyoap_vue_folder`, and `auto`.
