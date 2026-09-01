# GUI Queue Guide — CYOA Downloader v1.0.8

## Change a mode without removing the URL

1. Add the URL to the queue normally.
2. Select its mode badge, such as `auto`.
3. Choose the desired output mode.

The selected mode is stored on that job immediately. Its URL, queue order, and
filename remain unchanged. Selecting an explicit mode clears the previous
auto-detected result so the row is no longer presented as automatically
classified.

## Export and import the queue

1. Select **Export List…** in the Input area.
2. Choose `.csv` for spreadsheet-compatible data or `.txt` for a simple text
   representation.
3. Save the file and use **Import List…** to load it again later.

CSV columns:

```text
url,filename,mode
https://example.com/cyoa/,Example,website_folder
```

TXT representation:

```text
https://example.com/cyoa/ | Example | website_folder
```

The `auto` mode is preserved across export and import. Internal fields such as
the job identifier are intentionally not exported.
