# CYOA Downloader v7.2.1

CYOA Downloader is a Python desktop tool for downloading, packaging, and preserving Interactive CYOA projects for offline playback.

It supports multiple Interactive CYOA formats and viewer engines, including ICC Plus, ICC Remix, cyoap_vue, and custom React or Vue based CYOA pages. The tool can download project data, images, audio, fonts, scripts, styles, and full website assets, then package everything into offline friendly formats.

> This project is pure vibe code with Claude AI.  
> It was built, expanded, debugged, and documented through AI assisted development rather than a traditional software engineering workflow.

## What This Tool Does

CYOA Downloader helps users archive Interactive CYOAs so they can be opened later without relying on the original website staying online.

It can:

- Download Interactive CYOA projects from direct URLs
- Resolve cyoa.cafe iframe links automatically
- Handle archive.org CYOA catalog redirects
- Detect ICC compatible project JSON files
- Download images, audio, fonts, scripts, CSS, and site assets
- Package projects into multiple offline formats
- Create website folders that can be opened locally
- Inject projects into offline viewer packages
- Import downloaded projects into CYOA Manager
- Run from either GUI or CLI
- Process single URLs or batch queues

## Main Features

### Multiple Download Modes

CYOA Downloader supports several output modes:

| Mode | Description |
|---|---|
| Website Folder | Downloads the full site with viewer files and assets |
| Website ZIP | Same as Website Folder, but compressed into a ZIP archive |
| Embedded JSON | Creates a single JSON file with base64 embedded images |
| ZIP Archive | Saves `project.json` with a separate `images/` folder |
| Both | Creates both Embedded JSON and ZIP output |
| cyoap_vue Folder | Downloads cyoap_vue projects using `dist/platform.json` and node files |
| Offline Viewer Folder | Injects a downloaded project into a compatible offline viewer |

### Supported Sources

The tool supports:

- Direct Interactive CYOA URLs
- Neocities pages
- Netlify pages
- Vercel pages
- GitHub Pages
- cyoa.cafe links
- archive.org CYOA catalog links
- Sites that expose an ICC compatible `project.json`
- cyoap_vue based CYOA projects
- Custom React or Vue based CYOA websites when assets can be detected

### Asset Detection

The downloader scans for many types of assets, including:

- Main choice images
- Background images
- Row background images
- Object background images
- Icons
- Avatars
- Portraits
- Thumbnails
- Cover images
- Audio files
- Background music
- Sound effects
- Fonts
- CSS referenced assets
- Markdown image links
- HTML image tags inside JSON fields
- Relative asset paths inside project data

### Offline Viewer Support

The tool can inject downloaded CYOA data into offline viewer packages.

Supported viewer targets include:

- ICC Plus
- ICC Remix
- ICC Original compatible viewers
- New Viewer builds
- Custom offline viewer ZIP packages

The offline viewer system attempts multiple injection strategies, including template replacement, marker based injection, and fetch interception.

### GUI and CLI

You can use the tool in two ways:

- GUI mode for normal users
- CLI mode for automation, scripts, or batch workflows

Running the script without arguments opens the GUI by default.

## Installation

### Requirements

- Python 3.9 or newer
- Windows 10 or newer, macOS, or Linux
- Internet connection for downloading CYOA assets

Tested mainly with Python 3.10 to Python 3.12.

## Required Python Packages

Install the required dependencies:

```bash
pip install requests beautifulsoup4 customtkinter tldextract Pillow
```

## Optional Python Packages

These packages unlock extra features:

```bash
pip install cloudscraper yt-dlp plyer rarfile openpyxl
```

| Package | Feature |
|---|---|
| cloudscraper | Helps with Cloudflare protected sites |
| yt-dlp | Downloads YouTube or external audio when supported |
| plyer | Desktop notifications |
| rarfile | Supports RAR based offline viewer packages |
| openpyxl | Imports batch URLs from XLSX files |
| playwright or selenium | Optional browser based fallback |

## Optional System Dependencies

### FFmpeg

Required if you want `yt-dlp` to convert downloaded audio.

Install FFmpeg:

```bash
# Windows
winget install ffmpeg

# macOS
brew install ffmpeg

# Ubuntu or Debian
sudo apt install ffmpeg
```

### unrar

Required only if you want to use RAR based offline viewer packages.

```bash
# macOS
brew install unrar

# Ubuntu or Debian
sudo apt install unrar
```

## How to Run

### Start the GUI

```bash
python cyoa_downloader_v7_2_1.py
```

The GUI will open automatically.

### Use CLI Mode

```bash
python cyoa_downloader_v7_2_1.py --url "https://example.com/cyoa/" --output ./downloads
```

## Basic GUI Usage

1. Open the script:

```bash
python cyoa_downloader_v7_2_1.py
```

2. Paste the CYOA URL into the URL field.

3. Choose a download mode.

Recommended mode for most users:

```text
Website Folder
```

4. Choose the output folder.

5. Click Download.

6. Open the generated folder or file after the process finishes.

## Recommended Download Modes

### Best General Option

Use:

```text
Website Folder
```

This keeps the CYOA closest to the original website and is usually the safest choice for offline playback.

### Best for Sharing Project Data

Use:

```text
ZIP
```

This creates a smaller package with `project.json` and an `images/` folder.

### Best for Single File Backup

Use:

```text
Embedded JSON
```

This stores images as base64 inside one JSON file. It can be large, but it is easy to keep as a single backup file.

### Best for Maximum Backup

Use:

```text
Both
```

This creates both Embedded JSON and ZIP output.

### Best for cyoap_vue Projects

Use:

```text
cyoap_vue Folder
```

This mode downloads `dist/platform.json`, `dist/nodes/list.json`, node files, and detected assets.

## CLI Examples

### Download with Default Settings

```bash
python cyoa_downloader_v7_2_1.py --url "https://example.com/cyoa/" --output ./downloads
```

### Save as Embedded JSON

```bash
python cyoa_downloader_v7_2_1.py --url "https://example.com/cyoa/" --output ./downloads --mode embed
```

### Save as ZIP

```bash
python cyoa_downloader_v7_2_1.py --url "https://example.com/cyoa/" --output ./downloads --mode zip
```

### Save Both Embedded JSON and ZIP

```bash
python cyoa_downloader_v7_2_1.py --url "https://example.com/cyoa/" --output ./downloads --mode both
```

### Download Website Folder

```bash
python cyoa_downloader_v7_2_1.py --url "https://example.com/cyoa/" --output ./downloads --mode website_folder
```

### Download Website ZIP

```bash
python cyoa_downloader_v7_2_1.py --url "https://example.com/cyoa/" --output ./downloads --mode website_zip
```

## Batch Download

The GUI supports queue based downloading.

You can add multiple URLs, then download them one by one.

Supported batch sources include:

- TXT files
- CSV files
- XLSX files
- Google Sheets CSV export links

### TXT Format

```text
https://example.com/cyoa-one/
https://example.com/cyoa-two/ | Custom File Name
https://example.com/cyoa-three/ | Another Name | website_folder
```

### CSV or XLSX Columns

Supported column names:

| Column | Purpose |
|---|---|
| url or link | Required CYOA URL |
| filename or name or title | Optional output name |
| mode | Optional download mode |

Example:

```csv
url,filename,mode
https://example.com/cyoa-one/,CYOA One,website_folder
https://example.com/cyoa-two/,CYOA Two,zip
```

## CYOA Manager Integration

CYOA Downloader can register downloaded projects into CYOA Manager if it can find the local `library.sqlite3` database.

It can store:

- Project name
- Source URL
- Local file path
- Viewer preference
- Tags
- Date added

If the database is not detected automatically, you can set the path manually in the settings panel.

## AI Assist

AI Assist is an optional helper feature for difficult websites.

When enabled and configured with an API key, it can help analyze HTML or JavaScript to locate hidden project data or project URLs.

This is mainly useful when normal project detection fails.

## Download Pipeline

The downloader follows this general process:

1. Normalize the input URL
2. Resolve special links such as cyoa.cafe or archive.org
3. Detect the project type
4. Search for project JSON
5. Scan HTML and JavaScript bundles
6. Extract project data
7. Detect images, audio, fonts, and other assets
8. Download or embed assets depending on selected mode
9. Build the selected output format
10. Optionally inject the result into an offline viewer
11. Save metadata and history

## Local Preview Server

Some CYOAs do not work correctly when opened directly through `file://`.

For those cases, use the local preview server from the GUI, then open the generated local address in your browser.

This helps with projects that expect HTTP behavior, relative paths, scripts, or browser security rules.

## Troubleshooting

### The CYOA opens but images are missing

Try these steps:

1. Use Website Folder mode instead of Embedded JSON.
2. Enable deep scan if available.
3. Check whether the original site blocks hotlinking.
4. Try using cloudscraper.
5. Check the generated backup report.

### The website works online but not offline

Try:

```text
Website Folder
```

Then open it using the local preview server instead of opening the HTML file directly.

### Download fails on a Cloudflare protected website

Install cloudscraper:

```bash
pip install cloudscraper
```

Then enable Cloudflare bypass in the GUI if available.

### YouTube or external audio does not download

Install yt-dlp and FFmpeg:

```bash
pip install yt-dlp
```

Then install FFmpeg using your operating system package manager.

### RAR viewer package does not work

Install rarfile and unrar:

```bash
pip install rarfile
```

Then install `unrar` for your operating system.

### Batch import from XLSX does not work

Install openpyxl:

```bash
pip install openpyxl
```

## Known Limitations

This tool is not perfect.

Some CYOAs may still fail because:

- The source website blocks automated downloads
- The project uses heavily obfuscated JavaScript
- Assets are generated dynamically at runtime
- Assets require login, cookies, or special headers
- The CYOA depends on remote APIs
- The viewer format is custom and unsupported
- Browser security blocks local file playback
- Audio hosted on external platforms cannot always be preserved

## Development Status

Current version:

```text
v7.2.1
```

Current project scale:

```text
11,976 lines
336 functions
5 classes
```

Recent improvements include:

- Fixed non website download modes
- Fixed Embedded JSON mode
- Fixed ZIP mode
- Fixed Both mode
- Fixed offline viewer injection
- Improved deep scan performance
- Added better asset detection
- Added CYOA Manager import support
- Added speed graph support
- Added AI Assist pipeline
- Added stronger documentation

## Important Disclaimer

This project is pure vibe code with Claude AI.

That means the code was created and improved through experimental AI assisted development. It is useful, practical, and feature rich, but it should not be treated as professionally audited production software.

Use it carefully.  
Expect bugs.  
Back up your files.  
Read the logs when something breaks.

## Ethical Use

This tool is intended for personal archival, preservation, and offline access.

Please respect:

- Original CYOA creators
- Website terms of service
- Copyright rules
- Community sharing rules
- Private or restricted content

Do not use this tool to redistribute content without permission.

## Suggested Repository Structure

```text
CYOA-Downloader/
├── cyoa_downloader_v7_2_1.py
├── CYOA_Downloader_v721_Docs.html
├── README.md
├── requirements.txt
├── LICENSE
└── examples/
    └── batch_urls_example.txt
```

## requirements.txt

```text
requests
beautifulsoup4
customtkinter
tldextract
Pillow
cloudscraper
yt-dlp
plyer
rarfile
openpyxl
```

You can remove optional packages if you want a smaller installation.

## License

Choose a license that fits your release goal.

Recommended options:

- MIT License for open use
- GPLv3 if you want derivative works to stay open source
- No license if you do not want to grant reuse rights automatically

## Credits

Built with pure vibe code with Claude AI.

Created for Interactive CYOA preservation, offline playback, and personal archiving.
