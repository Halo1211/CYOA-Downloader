# Auto (Safe) Archive

`Auto` adalah coordinator, bukan engine downloader baru. Ia memilih jalur yang
paling ringan berdasarkan bukti yang tersedia:

1. `cyoa.cafe/game/<id>` dengan `cyoa_pages` → direct PocketBase files dan
   gallery offline tanpa aplikasi katalog.
2. Record dengan `iframe_url` → resolve viewer tujuan, lalu lanjutkan deteksi.
3. Project JSON valid → Classic/project-first; Browser dilewati.
4. Route cerita same-origin → Smart atau Browser jika signal runtime juga kuat.
5. SPA/Next/Vite/runtime tanpa project → Browser.
6. HTML/asset yang dapat discan → Classic.

## Sandbox interaksi aman

- Kandidat terbatas pada button/role-button/summary non-form yang terlihat.
- Allowlist: Load More, Show More, Next, Continue, Expand, Reveal, dan
  `aria-expanded=false`.
- Denylist: login, register, submit, send, report, like/vote, payment, donate,
  delete, share, upload, dan comment.
- Selama interaction phase, metode selain GET/HEAD serta navigasi dokumen
  diblokir. Popup dan dialog ditutup.
- Jumlah klik, scroll, runtime pages, dan no-progress rounds selalu dibatasi.

## Manifest

`archive_manifest.json` menyimpan `requested_policy`, policy efektif,
`auto_profile`, jumlah scroll, interaksi produktif, request yang diblokir, dan
alasan Browser dilewati. Ini membuat keputusan Auto dapat diaudit tanpa harus
menebak dari log.

## CYOA.CAFE

Static record menghasilkan:

- `index.html`
- `images/pages/`
- `images/previews/` bila tersedia
- `images/cover/` bila tersedia
- `cyoa_cafe_metadata.json` tanpa payload base64 besar
- `archive_manifest.json`

Linked viewer tetap memakai resolver lama. Ini mencakup viewer project-based,
project dengan gambar data-URI tertanam, dan viewer custom yang membutuhkan
fallback Browser.

## Profil contoh yang diuji

| URL/kategori | Sinyal utama | Jalur Auto |
|---|---|---|
| `cyoa.cafe/game/hl2exdb5mis2epn` | record `img`, satu `cyoa_pages` | adapter static PocketBase |
| `cyoa.cafe/game/5wo6xl14vnpjzpt` | record `link`, viewer eksternal, project besar dengan gambar data-URI | resolver + project-first |
| `cyoa.cafe/game/s8r10clavlh490j` | record `link`, 219 aset proyek | resolver + project-first |
| `starsheldaloft.cyoa.cafe/divinecontractcorporation/` | project JSON, 37 referensi aset | project-first |
| `powerpathcyoa.cyoa.cafe/path-of-power-cyoa-v-20-by-larien-static-complete/` | project JSON, 450 referensi aset | project-first |

Pada project-first, deep scan bundle sebelum dan sesudah lokalisasi sengaja
dilewati karena `process_images` sudah membaca struktur proyek. Ini mencegah
scan ganda, percobaan 404 dari string acak dalam JavaScript minified, serta
runtime Browser yang tidak menambah cakupan aset. HTML/CSS viewer tetap diproses
secara rekursif. Aset asal yang gagal diunduh dicatat sebagai dependency online
absolut dan masuk laporan kegagalan, bukan dibiarkan sebagai referensi lokal
yang menyesatkan.
