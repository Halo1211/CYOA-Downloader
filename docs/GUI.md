# GUI Guide

## Theme-compatible logo

The GUI titlebar loads `assets/logo-light.png` for light mode and `assets/logo-dark.png` for dark mode through `customtkinter.CTkImage`. If external assets are missing, the script uses embedded PNG data. If image loading fails, it falls back to a text mark.

## Mode labels

User-facing mode labels use ICC terminology:

- ICC MODE / MODE ICC
- ICC ZIP
- ICC Folder

Internal keys remain unchanged for compatibility:

- `website_zip`
- `website_folder`

## Recommended default

For general backups, **ICC Folder** is the safest default because it keeps HTML, CSS, JavaScript, images, fonts, audio, and project data inspectable before compression.
