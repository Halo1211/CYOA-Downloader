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
