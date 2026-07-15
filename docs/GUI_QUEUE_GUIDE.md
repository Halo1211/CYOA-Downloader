# GUI Queue Guide — CYOA Downloader v1.0.5

This page explains the queue controls in plain language. A queue is simply a
list of URLs that will be downloaded in order.

## Add a URL

1. Paste a CYOA URL into the URL field.
2. Optionally enter a filename.
3. Click **Add URL** or press Enter.

The new URL appears as a row in the queue. Duplicate URLs are allowed; each row
is treated as a separate job.

## Change a mode without removing the URL

Each row has a mode badge such as `auto`, `embed`, `website folder`, or
`pure website zip`.

1. Click the mode badge on the row you want to change.
2. Choose a mode from the menu.
3. Continue downloading normally.

The URL, filename, and row position stay unchanged. Choosing a mode manually
also clears an earlier auto-detect result so the row reflects your choice.

### Which mode should I choose?

| Goal | Mode |
| --- | --- |
| I am testing one normal CYOA | `auto` or `embed` |
| I want a playable folder | `website folder` |
| I want one file to store or share | `website zip` |
| The site is a custom viewer and project detection fails | `pure website folder` |
| The site uses CYOAP Vue | `cyoap vue folder` |

When unsure, use **website folder**. Folders are easier to inspect and retry
than ZIP files.

## Edit a filename

Edit the filename field directly under the URL before downloading. The change
is saved to that queue row immediately. Use simple names such as
`my_cyoa_backup`; the downloader will remove unsafe filesystem characters.

## Reorder or remove rows

- Drag the handle at the left of a row to change its priority.
- Click `×` on a row to remove only that row.
- Use **Clear All** to empty the queue.

## Export the queue

Use **Export List…** near the URL input to save the current queue.

1. Click **Export List…**.
2. Choose `.csv` for Excel/spreadsheet editing, or `.txt` for a simple text list.
3. Choose a filename and save.

CSV example:

```csv
url,filename,mode
https://example.com/cyoa/,my_backup,website_folder
https://example.com/another/,second_backup,auto
```

TXT example:

```text
https://example.com/cyoa/ | my_backup | website_folder
https://example.com/another/ | second_backup | auto
```

The export includes only `url`, `filename`, and `mode`. Internal queue IDs are
not included. `auto` is preserved when the file is imported again.

## Import a saved queue

Click **Import List…** and select a `.txt`, `.csv`, `.xlsx`, or `.xls` file.
The importer recognizes these column names:

- URL: `url`, `link`, `urls`, `links`
- Filename: `filename`, `name`, `output`, `title`, `file`
- Mode: `mode`, `output_mode`, `type`

If spreadsheet import is unavailable, run
`pip install -r requirements-optional.txt` from the repository root, or use
CSV/TXT instead.

## If a row fails

Read `backup_report.txt` first. Then use the matching retry button:

- **Retry Assets** for general failed assets;
- **Retry Images** for missing images/backgrounds;
- **Retry Audio** for audio that needs `yt-dlp` or FFMPEG.

For URL-level failures, reduce pressure by using fewer workers and a longer
retry wait. See [Troubleshooting](./TROUBLESHOOTING.md).
