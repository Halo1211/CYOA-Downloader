# Audit Komprehensif Arsip Universal

Dokumen ini mencatat area yang diperiksa untuk integrasi Classic/Smart/Browser
dan alasan perbaikannya. Tujuannya adalah menjaga konsep downloader lama sambil
menambahkan dukungan website JavaScript secara terukur.

## Temuan yang sudah diperbaiki

1. **Settings hasil edit manual dapat merusak tipe dan rentang nilai.** Loader
   sekarang menormalisasi boolean, integer, enum case-insensitive, temperatur
   AI, dan nilai null.
2. **Daftar enum settings tidak sama dengan opsi CLI/GUI.** Nilai Gallery-DL,
   sesi/proxy FlareSolverr, serta penyimpanan AI `env` kini dipertahankan.
3. **Depth nol berubah menjadi 30.** Normalisasi policy, default CLI, dan nilai
   awal kontrol GUI sekarang mempertahankan `archive_max_depth=0` sebagai
   permintaan hanya mengarsipkan halaman awal.
4. **Nama route dapat bertabrakan atau tidak valid di Windows.** Komponen path
   kini dibersihkan, nama perangkat Windows diamankan, Unicode dipertahankan,
   dan collision mendapat hash stabil.
5. **URL IPv6 rusak dapat menghentikan crawl.** Canonicalization, link parsing,
   rewrite, dan resume sekarang mengabaikan URL malformed secara aman.
6. **Batas halaman terlihat seperti crawl selesai.** Manifest kini mencatat
   `route_limit_reached` dan `remaining_queued_routes`.
7. **Resume rapuh terhadap manifest rusak dan path berbahaya.** Resume membangun
   recovery manifest, memvalidasi path lokal, mengabaikan file hilang, dan dapat
   menemukan link relatif yang belum diselesaikan.
8. **Capture runtime melewatkan aset tertentu.** Deteksi kini mengenali MIME
   image/audio/video/font, CSS, JavaScript, JSON, WASM, dan ekstensi aset saat
   server memberi MIME kosong atau keliru.
9. **Opsi interaksi membingungkan.** `archive_capture_interactions` dijelaskan
   sebagai field kompatibilitas. Browser mode hanya load, wait, dan scroll;
   tidak ada klik buta pada situs universal.

## Invarian kompatibilitas

- Classic tetap default dan menjalankan perilaku historis.
- Smart/Browser hanya aktif setelah dipilih pengguna.
- Crawler dibatasi origin, scope cerita, jumlah halaman, dan kedalaman.
- Aset runtime tetap melewati pipeline download normal.
- `settings.json` tetap berbentuk object flat; `_meta` hanya dokumentasi file dan
  tidak ikut menjadi runtime state.
- Secret tidak dicetak oleh dokumentasi atau tes, dan ekspor settings meredaksi
  key rahasia.

## Checklist verifikasi

Jalankan dari root project:

```powershell
python -m pytest -q
python -m pytest -q -W error
python -m compileall -q cyoa_downloader.py cyoa_downloader_app tests tools
python cyoa_downloader.py --self-test
python cyoa_downloader.py --dependency-check
python tools/audit_import_surface.py
python tools/audit_legacy_symbols.py
```

Audit parity terhadap file historis dapat melaporkan perbedaan signature yang
memang sengaja ditambahkan untuk parameter arsip. Perbedaan aditif tersebut
bukan kehilangan API lama selama semua nama wajib tetap tersedia.

## Kasus uji situs nyata

- Hypnosis Arena: pola daftar gambar JavaScript dan base path dinamis.
- Isekai Quest: rute cerita besar, Next.js image proxy, lazy/runtime assets, dan
  kebutuhan page limit yang lebih tinggi.

Hasil arsip harus diuji melalui server HTTP lokal. `file://` bukan indikator
yang andal untuk aplikasi modern karena module, fetch, CORS, dan service worker.

## Hasil verifikasi 13 Juli 2026

- Pytest normal: 175 lulus.
- Pytest dengan warning sebagai error: 175 lulus.
- Self-test internal: 37/37 lulus.
- Import package: 108 modul, 0 gagal.
- Import-surface audit: 15 nama wajib, 0 hilang.
- GUI smoke: main window, Feature Toggles, dan Help/Guide berhasil dirender.
- Dependency check: 19/19 modul Python terdeteksi; dependency wajib lengkap.
- Compileall dan legacy-symbol audit: lulus.
- Historical parity: 0 nama hilang, 0 constant diff, dan 5 signature diff yang
  seluruhnya merupakan parameter aditif arsip/security.
- Ruff tidak dijalankan karena modul opsional tersebut tidak terpasang.
