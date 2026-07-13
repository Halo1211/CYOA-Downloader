"""Asset-related constants and regexes."""

from __future__ import annotations

import re
from typing import List, Set

IMAGE_FIELDS: List[str] = [
    "image",               # primary image on choices and rows
    "backgroundImage",     # row/section background
    "rowBackgroundImage",  # row-level background
    "objectBackgroundImage",# object/choice background
    "defaultImage",        # ICC Plus: image shown when choice is NOT selected
    # Additional fields found in the wild
    "bgImage",             # shorthand used by some CYOA creators
    "bg",                  # ultra-short shorthand
    "img",                 # ultra-short shorthand
    "thumbnail",           # preview thumbnail
    "coverImage",          # cover/banner image
    "headerImage",         # header image
    "icon",                # choice icon
    "portrait",            # character portrait
    "avatar",              # character avatar
    "picture",             # generic picture field
    # ICC Plus / Svelte viewerConfig and imageSeparation keys
    "addonBackgroundImage",  # ICC Plus addon background
    "rowBorderImage",       # ICC Plus row border image
    "objectBorderImage",    # ICC Plus choice/object border image
    "addonBorderImage",     # ICC Plus addon border image
    "backpackBgImage",      # ICC Plus backpack background
    "loadingBgImage",       # ICC Plus loading screen background
    "favicon",              # viewerConfig favicon
    "negativeImage",        # point type negative image
    "selectedImage",        # selected-state image variants
    "unselectedImage",      # unselected-state image variants
    "borderImage",          # generic border image
    "loadingImage",         # generic loading image
    "rowImage",             # generic row image
    "choiceImage",          # generic choice/object image
]

ICC_PLUS_IMAGE_KEYS: Set[str] = {
    "image", "backgroundimage", "rowbackgroundimage", "objectbackgroundimage",
    "defaultimage", "addonbackgroundimage", "rowborderimage",
    "objectborderimage", "addonborderimage", "backpackbgimage",
    "loadingbgimage", "favicon", "negativeimage", "selectedimage",
    "unselectedimage", "borderimage", "loadingimage", "rowimage",
    "choiceimage",
}

AUDIO_FIELDS: List[str] = [
    "audio",           # soundEffects[].audio in ICC Plus (direct URL / base64)
    "audioSrc",        # generic alt name used by some custom viewers
    "backgroundMusic", # some custom viewers
    "backgroundAudio", # some custom viewers
    "rowAudio",        # hypothetical row-level audio
    "objectAudio",     # hypothetical object-level audio
    # Expanded: discovered in the wild
    "soundEffect",     # click/hover sound effects
    "sfx",             # shorthand for sound effects
    "bgm",             # background music shorthand
    "ambience",        # ambient audio tracks
    "voiceover",       # narration audio
    "narration",       # narration audio
    "soundFile",       # generic sound file reference
    "audioFile",       # generic audio file reference
    "musicFile",       # music file reference
    "clickSound",      # UI click sounds
    "hoverSound",      # UI hover sounds
    "selectSound",     # selection sound effects
    "musicUrl",        # direct music URL
    "audioUrl",        # direct audio URL
    "soundUrl",        # direct sound URL
    # NOTE: "bgmId" is intentionally excluded here — handled separately
    # by _deep_scan_project_assets() because the same field holds either a
    # YouTube ID (skip) or a direct URL depending on sibling "useAudioURL" field.
]

BGMLIST_FIELDS: Set[str] = {
    "bgmlist", "bgmplaylist", "bgmtracks", "playlist",
    "audiolist", "musiclist", "bgmqueue",
}

_YOUTUBE_URL_RE = re.compile(
    r'(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/',
    re.IGNORECASE,
)

_YOUTUBE_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')

_SOUNDCLOUD_URL_RE = re.compile(
    r'(?:https?://)?(?:www\.)?soundcloud\.com/',
    re.IGNORECASE,
)

FONT_EXTENSIONS: Set[str] = {".woff", ".woff2", ".ttf", ".otf", ".eot"}

IMAGE_EXTENSIONS: Set[str] = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".ico"}

AUDIO_EXTENSIONS: Set[str] = {".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac", ".opus", ".weba"}

VIDEO_EXTENSIONS: Set[str] = {".mp4", ".webm", ".ogv", ".mkv", ".mov", ".m4v"}

SCRIPT_EXTENSIONS: Set[str] = {".js", ".mjs"}

STYLE_EXTENSIONS: Set[str] = {".css"}

TEXT_ASSET_EXTENSIONS: Set[str] = SCRIPT_EXTENSIONS | STYLE_EXTENSIONS | {".html", ".json"}

__all__ = ['IMAGE_FIELDS', 'ICC_PLUS_IMAGE_KEYS', 'AUDIO_FIELDS', 'BGMLIST_FIELDS', '_YOUTUBE_URL_RE', '_YOUTUBE_ID_RE', '_SOUNDCLOUD_URL_RE', 'FONT_EXTENSIONS', 'IMAGE_EXTENSIONS', 'AUDIO_EXTENSIONS', 'VIDEO_EXTENSIONS', 'SCRIPT_EXTENSIONS', 'STYLE_EXTENSIONS', 'TEXT_ASSET_EXTENSIONS']
