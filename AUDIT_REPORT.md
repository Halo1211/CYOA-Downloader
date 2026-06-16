# CYOA Downloader v1.0 Release — Audit Report

## 1. Ringkasan audit

Audit dilakukan pada file sumber `cyoa_downloader_v7_6_1_stabilized_patch_v46_11.py`, kemudian hasil stabilisasi diterapkan ke `cyoa_downloader.py` untuk paket rilis GitHub.

Fokus rilis ini:

- Branding final menjadi **v1.0 Release**.
- Konsistensi istilah user-facing dari Website Mode menjadi ICC Mode.
- Penghapusan flag CLI lama `--website`, `-W`, dan `--website-folder`.
- Penambahan flag CLI resmi `--icc` dan `--icc-folder`.
- Pemeliharaan internal key lama `website_zip` dan `website_folder` agar batch/settings/manifest lama tetap aman.
- Integrasi logo project yang kompatibel dengan mode dark dan light.
- Persiapan struktur GitHub, MIT License, dokumentasi, requirements, dan test scaffold.

## 2. Daftar bug yang ditemukan

### Bug 1 — Branding versi lama belum sesuai rilis final

- Lokasi bug: konstanta `_APP_VERSION`, `_STABILIZATION_PATCH_ID`, title GUI, help text, userscript report.
- Dampak: rilis GitHub terlihat masih sebagai patch internal, bukan v1.0 Release.
- Penyebab: string versi masih mengacu ke `7.6.1` dan patch id stabilisasi.
- Solusi: ubah branding ke `1.0 Release` dan patch id ke `CYOA-v1.0-RELEASE`.
- Patch/perubahan kode: update konstanta versi dan teks user-facing.
- Risiko regresi: rendah; hanya branding, metadata, dan teks.
- Cara verifikasi: `python cyoa_downloader.py --help` menampilkan v1.0 Release pada teks terkait.

### Bug 2 — Flag lama Website masih aktif dan membingungkan

- Lokasi bug: `argparse` CLI parser.
- Dampak: user-facing naming tidak konsisten karena `--website`, `-W`, dan `--website-folder` masih muncul.
- Penyebab: flag lama dipertahankan sebagai alias dalam satu `add_argument`.
- Solusi: hapus flag lama dan gunakan hanya `--icc` serta `--icc-folder`.
- Patch/perubahan kode: parser sekarang hanya mendaftarkan `--icc` dan `--icc-folder` untuk destination lama.
- Risiko regresi: sedang untuk script automation lama; diterima karena user meminta flag lama dihapus.
- Cara verifikasi: `--icc` memetakan ke `website_zip`; `--icc-folder` memetakan ke `website_folder`; `--website` exit code 2.

### Bug 3 — Label GUI/help masih campur Website dan ICC

- Lokasi bug: GUI mode list, feature guide, help text, CLI examples, batch guide.
- Dampak: pengguna bisa mengira Website Mode dan ICC Mode adalah dua fitur berbeda.
- Penyebab: rename terminologi belum dilakukan menyeluruh.
- Solusi: ubah label user-facing menjadi ICC ZIP, ICC Folder, ICC Mode, MODE ICC.
- Patch/perubahan kode: update label GUI, panel mode, deskripsi, help, dan command example.
- Risiko regresi: rendah; internal key tidak diubah.
- Cara verifikasi: grep tidak menemukan `Website ZIP`, `Website Folder`, `WEBSITE MODE`, `--website`, atau `-W` selain konteks Pure Website yang memang fitur terpisah.

### Bug 4 — Import BeautifulSoup bisa membuat program gagal start

- Lokasi bug: import global `from bs4 import BeautifulSoup`.
- Dampak: program bisa crash saat startup jika `beautifulsoup4` belum terpasang, padahal fitur lain mungkin masih dapat digunakan.
- Penyebab: dependency HTML parser dipanggil secara wajib pada import module.
- Solusi: ubah menjadi import opsional dengan fallback function yang memberi pesan instalasi jelas.
- Patch/perubahan kode: fallback error: `Missing dependency: beautifulsoup4 ... pip install beautifulsoup4`.
- Risiko regresi: rendah; saat dependency tersedia perilaku tetap sama.
- Cara verifikasi: syntax check lolos dan `--help` tetap berjalan.

### Bug 5 — Logo GUI masih placeholder dan tidak theme-aware

- Lokasi bug: titlebar GUI, frame logo lama `C↯`.
- Dampak: branding rilis GitHub tidak masuk ke program dan kurang kompatibel dengan mode dark/light.
- Penyebab: logo hanya berupa label teks statis.
- Solusi: tambahkan asset logo light/dark, loader eksternal, embedded fallback, dan text fallback.
- Patch/perubahan kode: `_load_logo_images()`, `_APP_LOGO_LIGHT_B64`, `_APP_LOGO_DARK_B64`, dan `CTkImage(light_image=..., dark_image=...)`.
- Risiko regresi: rendah; jika Pillow/customtkinter image gagal, fallback teks tetap tampil.
- Cara verifikasi: syntax check, asset files tersedia, loader tidak mengubah alur download.

### Bug 6 — Batch mode belum menerima alias ICC baru

- Lokasi bug: `valid_modes`, `import_queue_items_from_file`, `import_queue_items_from_source`, dan batch processing.
- Dampak: pengguna baru yang menulis `icc_folder` di batch CSV/TXT bisa mengalami fallback mode default.
- Penyebab: batch value lama hanya mengenal `website_zip`/`website_folder`.
- Solusi: tambahkan alias `icc`, `icc_zip`, `icc_folder` tanpa mengubah internal key lama.
- Patch/perubahan kode: alias CSV/remote dinormalisasi ke `website_zip`/`website_folder`; TXT raw value tetap diterima di batch processing.
- Risiko regresi: rendah; values lama tetap didukung.
- Cara verifikasi: test batch import dan mode alias berjalan.

## 3. Patch/perubahan kode yang dilakukan

- Set `_APP_VERSION = "1.0 Release"`.
- Set `_STABILIZATION_PATCH_ID = "CYOA-v1.0-RELEASE"`.
- Removed CLI aliases: `--website`, `-W`, `--website-folder`.
- Added/retained CLI flags: `--icc`, `--icc-folder`.
- Kept internal mode keys: `website_zip`, `website_folder`.
- Added batch aliases: `icc`, `icc_zip`, `icc_folder`.
- Updated GUI sidebar to **ICC MODE**, **ICC ZIP**, and **ICC Folder**.
- Added light/dark logo assets and GUI loader.
- Added graceful BeautifulSoup fallback.
- Updated README, installation, usage, credits, changelog, release notes, MIT License, requirements, and tests.

## 4. File final yang sudah diperbaiki

Main file:

- `cyoa_downloader.py`

Repository docs/assets/tests:

- `assets/logo-light.png`
- `assets/logo-dark.png`
- `README.md`
- `CHANGELOG.md`
- `LICENSE`
- `CREDITS.md`
- `INSTALLATION.md`
- `USAGE.md`
- `docs/CLI.md`
- `docs/GUI.md`
- `docs/CREDITS.md`
- `requirements.txt`
- `requirements-dev.txt`
- `.gitignore`
- `tests/test_cli_args.py`
- `tests/test_mode_aliases.py`
- `tests/test_paths.py`
- `tests/test_batch_import.py`

## 5. Testing yang dilakukan

```bash
python -m py_compile cyoa_downloader.py
python cyoa_downloader.py --help
python -m pytest -q
```

Hasil lokal:

```text
9 passed in 13.73s
```

Manual mapping check:

| Input | Result |
| --- | --- |
| `--icc` | `website_output=True`, `website_zip_output=True` |
| `--icc-folder` | `website_output=True`, `website_zip_output=False` |
| `--website` | Removed; argparse exit code 2 |
| `-W` | Removed; argparse exit code 2 |

## 6. Risiko yang masih tersisa

- Penghapusan flag lama dapat memutus automation lama yang masih memakai `--website`, `-W`, atau `--website-folder`.
- Live download tetap perlu diuji pada Windows, Linux, dan macOS karena network behavior host CYOA bisa berbeda.
- Browser recovery seperti Selenium/Playwright bergantung pada driver/browser lokal.
- Beberapa optional dependency mungkin perlu instalasi manual sesuai fitur yang dipakai.
- GUI rendering logo perlu dicek manual pada Windows scaling tinggi dan Linux desktop environment tertentu.

## 7. Rekomendasi pengembangan berikutnya

- Pisahkan parser CLI ke fungsi `build_arg_parser()` agar unit test lebih ringan dan tidak perlu menjalankan `main()`.
- Tambahkan CI GitHub Actions untuk syntax check, pytest, and packaging smoke test.
- Tambahkan mode `--dry-run` untuk memverifikasi URL, output path, dan mapping mode tanpa melakukan download.
- Pisahkan engine downloader ke modul agar maintainability meningkat tanpa mengubah format output.
- Tambahkan screenshot GUI resmi ke folder `screenshots/` setelah diuji manual di Windows.
