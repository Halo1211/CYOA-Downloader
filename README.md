# CYOA Downloader v1.0.8

CYOA Downloader menyimpan CYOA/ICC dan website interaktif sebagai JSON, ZIP,
atau folder viewer offline. Untuk website yang membentuk halaman dan aset lewat
JavaScript, program menyediakan empat strategi arsip tanpa mengubah alur lama.

## Mulai cepat dari GUI

```powershell
python cyoa_downloader.py
```

Untuk website seperti Faproulette, Hypnosis Arena, atau CYOA.CAFE:

1. Pilih **Pure Website Folder**.
2. Klik **Settings**.
3. Pada **Kebijakan Arsip JavaScript**, pilih **Auto**.
4. Isi **Maks. halaman** dan **Maks. kedalaman**, lalu klik **Simpan kebijakan arsip**.
5. Tambahkan URL ke antrean dan jalankan **Download All**.
6. Buka hasil melalui tombol **Serve**; jangan mengandalkan `file://` untuk
   aplikasi JavaScript modern.

Rekomendasi awal untuk cerita besar: 800 halaman dan kedalaman 30. Batas ini
bukan target yang wajib dihabiskan; crawler berhenti ketika tidak ada rute baru.

## Strategi arsip website

- **Classic** — perilaku historis satu halaman; tersedia sebagai pilihan manual.
- **Smart** — Classic ditambah crawl rute cerita same-origin yang dibatasi.
- **Browser** — Smart ditambah capture aset yang baru terlihat ketika
  JavaScript dijalankan.
- **Auto** — mode default; mengenali project JSON, record CYOA.CAFE, route tree, dan runtime
  framework lalu memilih pipeline paling ringan yang tetap lengkap.

Mode Browser cocok untuk lazy loading, daftar gambar yang dirangkai JavaScript,
SPA/Next.js, dan aset yang baru diminta setelah halaman dirender. Program tidak
menganggap login, API autentikasi, telemetri, atau domain luar sebagai rute
cerita.

Panduan lengkap: [JavaScript Archive Guide](docs/JAVASCRIPT_ARCHIVE_GUIDE.md).

## Jaringan lanjutan: proxy, DNS, dan VPN guard

Buka **Settings → Jaringan** untuk memilih mode proxy, override HTTP/HTTPS,
daftar bypass, transport DNS (`system`, UDP, TCP, DoH, atau DoT), timeout,
fallback DNS sistem, IPv6, dan kebijakan VPN guard. Preset Cloudflare
`1.1.1.1`, Google, Quad9, BebasDNS, DoH, dan DoT tersedia langsung di dropdown;
resolver lain tetap dapat dimasukkan melalui **Custom…**. Tombol **Validasi offline**
memeriksa format konfigurasi dan interface lokal tanpa mengirim request ke
situs pihak lain. Kontrol Proxy/DNS ringkas di GUI utama tetap tersinkron.

VPN guard tidak membuat atau menyalakan tunnel. Mode `system` mengikuti routing
OS; mode `require` menghentikan request downloader bila interface VPN aktif
yang cocok tidak terdeteksi. Untuk SOCKS, `socks5h://` disarankan bila nama host
juga harus di-resolve lewat proxy.

Contoh CLI DoH + proxy SOCKS + VPN wajib:

```powershell
python cyoa_downloader.py URL `
  --dns-protocol doh `
  --dns https://cloudflare-dns.com/dns-query `
  --no-dns-fallback-system `
  --proxy-mode manual `
  --proxy socks5h://127.0.0.1:1080 `
  --vpn-policy require `
  --vpn-interface WireGuard
```

Panduan dan matriks opsi lengkap: [Network, DNS, Proxy & VPN Guide](docs/NETWORK_GUIDE.md).

## Antrean GUI dan ekspor list (v1.0.8)

Mode setiap URL dapat diubah langsung dari baris antrean. Klik badge mode seperti
**auto**, lalu pilih mode baru; URL dan nama file tetap dipertahankan sehingga
tidak perlu menghapus dan menambahkan ulang job.

Tombol **Export List…** menyimpan seluruh antrean sebagai CSV atau TXT. Data
yang disimpan adalah `url`, `filename`, dan `mode`, sehingga file tersebut dapat
diimpor kembali melalui **Import List…** atau diedit sebelum dipakai di mesin
lain. Panduan singkat tersedia di
[GUI Queue Guide](docs/GUI_QUEUE_GUIDE.md).

## CLI

```powershell
python cyoa_downloader.py "https://example.com/story/" `
  --pure-website-folder `
  --archive-strategy auto `
  --archive-max-pages 800 `
  --archive-max-depth 30 `
  -o "hasil"
```

Jalankan `python cyoa_downloader.py --help` untuk seluruh mode dan opsi.

## Memulihkan gambar Discord

Fitur ini sudah menjadi bagian dari program utama dan ditulis ulang secara
internal; tidak perlu memasang Discord SDK atau menjalankan aplikasi kedua.
Panduan lengkap tersedia di
[Panduan Pemulihan Attachment Discord](docs/DISCORD_ATTACHMENTS_GUIDE.md).
Fitur berjalan di pipeline download biasa, baik saat data berasal dari
`project.json` maupun saat project disembunyikan di bundle `.js`:

```powershell
$env:DISCORD_BOT_TOKEN = "TOKEN_BOT_DISCORD"
python cyoa_downloader.py "https://example.com/cyoa/" -o "hasil"
```

Program mencoba URL CDN secara langsung terlebih dahulu. Jika URL sudah
kedaluwarsa, program memanggil endpoint refresh Discord API v10 dan mengulangi
unduhan. Hasil project menunjuk ke gambar lokal seperti aset lain. CLI juga
mendukung `--discord-token TOKEN` untuk token satu proses dan
`--no-discord-refresh` untuk menonaktifkan fitur sementara.

### Login dari GUI

Pada GUI buka **Settings / Maintenance**. Kontrol **Discord Bot Token** berada
langsung di halaman Settings; tidak ada panel Discord terpisah. Ini bukan login
akun Discord biasa: masukkan **Bot Token** dari Discord Developer Portal,
tekan **Simpan**, lalu **Tes**. Token ditulis langsung ke `settings.json`; tidak
ada pilihan mode penyimpanan. Untuk JSON yang sudah
berisi URL `cdn.discordapp.com/attachments/...`, bot milikmu tidak perlu masuk
ke server owner CYOA karena program tidak membaca channel atau pesan. Setelah
itu gunakan tombol **Download** biasa. Tidak ada input/output JSON, checkbox
aktivasi, atau tombol recovery Discord terpisah.

Akses server hanya diperlukan jika input yang tersedia cuma link pesan Discord
dan program harus membaca ulang pesan tersebut. Mode saat ini bekerja langsung
dari URL attachment di JSON.

Jangan memasukkan password Discord, user token, atau token akun pribadi.

## Settings

Setting aktif berada di `~/.cyoa_downloader/settings.json`. Formatnya tetap flat
agar kompatibel dengan versi lama, tetapi setiap kategori diberi penanda
`_section_...` dan memiliki `_meta` yang menjelaskan mode arsip serta rentang
nilai. Penanda tersebut hanya judul visual dan diabaikan saat program membaca
setting. Nilai hasil edit manual yang tidak valid dinormalisasi ke nilai aman.

Bot Token Discord berada di field `discord_bot_token` dan disimpan langsung di
file ini. Tidak ada `discord_token_storage` atau pilihan mode penyimpanan.

`archive_interaction_policy=safe` mengizinkan tombol non-form yang masuk
allowlist seperti Load More/Show More. Request mutasi, navigasi, popup, login,
komentar, vote, report, pembayaran, dan submit diblokir. Field lama
`archive_capture_interactions` tetap dibaca untuk kompatibilitas.

Cloudflare dapat diatur dari panel **Cloudflare Access** atau CLI. Mode `Auto`
mengirim request normal terlebih dahulu dan baru memakai fallback ketika
challenge benar-benar terdeteksi. Prioritas fallback dapat dipilih melalui
`cloudflare_priority=flaresolverr_first` atau `cloudscraper_first`, maupun CLI:

```powershell
python cyoa_downloader.py URL --cloudflare auto --cloudflare-priority flaresolverr-first
```

Retry audio dan image memproses ulang log kegagalan secara background, menutup
response HTTP dengan benar, dan memperbarui referensi `project.json` setelah
audio berhasil disimpan. Dua baris antrean dengan URL CYOA yang sama dianggap
dua job terpisah dan tidak saling menghapus saat batch selesai.

Gunakan penyimpanan `session` atau `keyring` untuk secret. Jangan membagikan
`settings.json` aktif jika memilih penyimpanan API key `plain`. Fitur ekspor
settings membuang key rahasia secara otomatis.

## Verifikasi

```powershell
python -m pytest -q
python cyoa_downloader.py --self-test
python cyoa_downloader.py --dependency-check
```

Dokumen refactor/pemeriksaan internal tetap tersedia di
[README_REFACTOR.md](README_REFACTOR.md) dan folder [docs](docs/).
