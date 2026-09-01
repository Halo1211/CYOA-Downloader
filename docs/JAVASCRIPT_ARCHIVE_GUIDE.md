# Panduan Arsip Website JavaScript

Panduan ini menjelaskan pemilihan mode, batas crawl, hasil arsip, dan aturan
operasional untuk website CYOA yang memuat konten setelah JavaScript berjalan.

## Memilih mode

| Mode | Yang dilakukan | Gunakan ketika |
|---|---|---|
| Classic | Download halaman dan aset statis dengan alur lama | ICC biasa atau kompatibilitas maksimum |
| Smart | Classic + crawl rute cerita same-origin | Pilihan cerita berpindah URL/halaman |
| Browser | Smart + observasi respons runtime | Lazy loading, SPA, Next.js, atau array aset JavaScript |
| Auto | Fingerprint + adapter terstruktur + fallback di atas | Pilihan utama untuk URL yang belum dikenal |

Classic tetap default. Auto, Smart, dan Browser bersifat tambahan, sehingga pengguna
lama tidak mendapatkan crawl besar tanpa memilihnya sendiri.

## Penggunaan GUI

1. Pilih **Pure Website Folder** untuk website custom tanpa `project.json`.
2. Buka **Settings → Kebijakan Arsip JavaScript**.
3. Pilih **Auto** untuk deteksi otomatis, atau strategi eksplisit untuk debugging.
4. Atur **Maks. halaman** (`1..5000`) dan **Maks. kedalaman** (`0..100`).
5. Pilih interaksi **Safe** atau **Off**, lalu atur batas scroll/klik.
6. Klik **Simpan kebijakan arsip**, lalu jalankan antrean seperti biasa.

Semua angka pada kartu adalah **batas pengaman**, bukan target yang wajib
dihabiskan. Crawler dan browser runtime berhenti lebih awal jika tidak menemukan
rute atau aset baru.

## Penjelasan setiap opsi

| Opsi | Fungsi | Dampak dan saran |
|---|---|---|
| **Strategi: Auto** | Memprofilkan project data, record CYOA.CAFE, route, dan sinyal runtime lalu memilih pipeline lengkap yang paling ringan | Pilihan utama untuk URL baru. Jika hasil kurang lengkap, baca `archive_manifest.json` sebelum memaksa Browser |
| **Strategi: Classic** | Mengunduh halaman awal dan aset statis dengan alur historis | Paling ringan dan kompatibel, tetapi tidak mengikuti pilihan antarhalaman atau aset yang baru muncul saat JavaScript berjalan |
| **Strategi: Smart** | Classic ditambah crawl rute cerita same-origin | Cocok jika tombol/pilihan berpindah URL tetapi aset tidak memerlukan render browser |
| **Strategi: Browser** | Smart ditambah render JavaScript, observasi respons, lazy-load scroll, dan interaksi aman opsional | Paling lengkap tetapi paling mahal; gunakan eksplisit untuk debugging atau saat Auto belum menangkap aset runtime |
| **Interaksi: Safe** | Melakukan scroll bertahap dan klik pada kontrol non-form dalam allowlist | Request mutasi dan navigasi berbahaya diblokir. Login, submit, payment, upload, delete, vote, report, dan kontrol serupa tidak diklik |
| **Interaksi: Off** | Merender dan scroll tetapi tidak mengeklik kontrol | Gunakan jika klik dapat mengubah state situs atau konten cukup muncul lewat load/scroll |
| **Maks. halaman** | Batas keras rute same-origin yang boleh disimpan | Naikkan hanya bila manifest menunjukkan crawl berhenti tepat di batas; terlalu besar menambah waktu, disk, dan risiko loop situs |
| **Maks. kedalaman** | Jumlah maksimum lompatan rute dari halaman awal | `0` berarti hanya rute awal. Depth `30` merupakan titik awal praktis untuk cerita bercabang besar |
| **Halaman runtime** | Jumlah maksimum rute yang dibuka di mesin browser | Jaga jauh lebih kecil dari Maks. halaman karena render browser adalah tahap paling berat |
| **Waktu tunggu (ms)** | Waktu menunggu setelah load, scroll, atau klik aman | Naikkan untuk situs dengan animasi/request lambat; turunkan jika situs cepat dan jumlah halaman runtime besar |
| **Langkah scroll** | Maksimum scroll bertahap untuk memicu lazy loading | Proses berhenti lebih awal jika tidak ada progres; angka tinggi hanya berguna untuk halaman sangat panjang |
| **Maks. klik aman** | Maksimum klik allowlist per halaman runtime | `0` menonaktifkan klik tanpa mematikan render/scroll. Gunakan kecil kecuali cerita menyembunyikan banyak cabang di accordion/tab |
| **Putaran tanpa progres** | Jumlah putaran berturut-turut tanpa rute, aset, atau respons berguna baru sebelum berhenti | Nilai `2` cepat dan aman; naikkan menjadi `3–4` untuk situs yang memunculkan konten secara lambat/bertahap |

Hubungan batas yang penting:

- **Maks. halaman** membatasi keseluruhan crawl, sedangkan **Halaman runtime**
  hanya membatasi subset yang benar-benar dirender browser.
- **Maks. kedalaman** membatasi jarak dari entry; jumlah halaman tetap dibatasi
  lagi oleh **Maks. halaman**.
- **Langkah scroll**, **Maks. klik aman**, **Waktu tunggu**, dan **Putaran tanpa
  progres** hanya memengaruhi tahap runtime Browser/Auto yang memilih Browser.
- Mode **Safe** bukan otomatisasi bebas: kontrol berisiko tetap diblokir dan
  request non-GET/HEAD tidak diizinkan oleh sandbox interaksi.

## Preset awal

- Website satu halaman dengan gallery runtime: Browser, 50 halaman, depth 10.
- Cerita bercabang sedang: Auto + Safe, 300 halaman, depth 30, runtime 12.
- Cerita besar seperti Isekai Quest: Auto + Safe, 800 halaman, depth 30,
  runtime 20.
- Situs yang cukup dengan link biasa: Smart, 300 halaman, depth 30, interaksi Off.

`max_pages` adalah pagar keselamatan, bukan jumlah halaman yang dipaksakan.
`max_depth=0` hanya mengarsipkan halaman awal. Runtime rendering dibatasi lagi
agar Browser tidak membuka ratusan halaman secara headless tanpa kebutuhan.

## Cara kerja

1. Downloader Classic menyimpan entry HTML serta aset yang dapat ditemukan.
2. Auto profiler memeriksa metadata CYOA.CAFE, project JSON, route, dan signal runtime.
3. Scanner membaca HTML, CSS, JavaScript, `srcset`, dan pola aset dinamis.
4. Smart mengikuti link cerita same-origin dalam scope URL awal.
5. Setiap route dipetakan ke `routes/<slug>/index.html` dan link ditulis ulang.
6. Browser menyapu viewport bertahap dan memasukkan respons aset yang
   diamati ke pipeline downloader normal.
7. Safe interaction hanya mencoba kontrol allowlist dalam sandbox read-mostly.
8. `archive_manifest.json` mencatat policy, profil Auto, mapping route, kegagalan route, dan
   hasil runtime capture.
9. Pemeriksa integritas memvalidasi file yang benar-benar menjadi dependency,
   bukan kata `href`/`url` acak di dalam kode JavaScript.

## Cakupan dan pembatasan

- Crawl hanya mengikuti HTTP(S), same-origin, dan scope cerita awal.
- Link login, autentikasi, API, mail, telemetri, serta domain luar tidak dianggap
  route cerita.
- Query yang mengubah isi dipertahankan dalam cache key/nama lokal; query cache
  buster umum boleh dinormalisasi.
- Next.js `/_next/image` dibuka ke URL gambar asli, termasuk `srcset` dan
  `imagesrcset`.
- Program tidak melewati CAPTCHA, paywall, age verification, atau login.
- Browser mode melakukan load, menunggu, dan incremental scroll untuk memicu
  lazy loading. Safe interaction tidak mengeklik secara buta: form dan input
  submit tidak disentuh; request selain GET/HEAD dan navigasi diblokir; label
  login/send/report/vote/payment/delete/upload/comment ditolak.
- Record CYOA.CAFE dengan `cyoa_pages` diekspor menjadi gallery offline langsung.
  Record dengan `iframe_url` tetap diarahkan ke viewer aslinya agar project JSON
  atau engine interaktif tidak salah dianggap sebagai gambar statis.
- Website yang bergantung pada backend pribadi tidak dapat dibuat sepenuhnya
  offline hanya dengan menyalin frontend.

## Membaca hasil

Gunakan server lokal dari GUI (**Serve**) atau server HTTP lain. File penting:

- `index.html` — entry arsip.
- `routes/.../index.html` — halaman cerita hasil crawl.
- `archive_manifest.json` — mapping dan status tahap arsip.
- `backup_report.txt`/failure log — kegagalan aset dari pipeline lama jika ada.

Membuka dengan `file://` dapat gagal karena aturan module, CORS, fetch, dan
service worker browser. Kegagalan tersebut tidak selalu berarti file arsip hilang.

## Troubleshooting

- **Crawl berhenti tepat di batas:** naikkan Maks. halaman dan ulangi output baru.
- **Gambar baru muncul setelah klik:** gunakan Browser; interaksi kompleks masih
  dapat membutuhkan workflow khusus situs.
- **Halaman awal ada tetapi pilihan rusak:** periksa `archive_manifest.json` dan
  pastikan output dijalankan lewat Serve.
- **Banyak 404 yang tampak seperti potongan kode:** jalankan integrity checker
  versi terbaru; validator lama dapat salah membaca ekspresi JavaScript.
- **Situs Cloudflare:** atur Cloudflare/FlareSolverr dari Settings dan ulangi.

## Guideline pengembangan

- Jangan mengubah default Classic atau kontrak output lama tanpa migrasi.
- Semua tahap universal harus bounded: same-origin, scope, page limit, depth,
  settle timeout, dan runtime page limit.
- Semua aset runtime harus melewati pipeline download normal agar retry,
  SSRF guard, cache, collision handling, dan report tetap berlaku.
- Jangan menulis ulang string dinamis jika browser masih perlu menggabungkannya.
- Tambahkan tes regresi untuk setiap pola framework/situs yang menimbulkan bug.
- Uji hasil melalui HTTP lokal dan periksa console serta dependency lokal.
