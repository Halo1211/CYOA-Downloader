# Panduan Network, DNS, Proxy & VPN Guard

Pengaturan berada di **Settings → Jaringan**. Setelah mengubah nilai, tekan
**Simpan & terapkan**, lalu **Validasi offline**. Validasi hanya memeriksa
konfigurasi dan interface lokal; tidak ada request tes ke situs pihak lain. Profil disimpan di
`~/.cyoa_downloader/settings.json` dan dipakai oleh GUI utama maupun CLI.

## Proxy

Mode yang tersedia:

- `inherit_env`: memakai `HTTP_PROXY`, `HTTPS_PROXY`, atau `ALL_PROXY` dari
  environment.
- `manual`: memakai Common proxy dan, bila diisi, override HTTP/HTTPS.
- `disabled`: tidak memakai proxy manual maupun proxy environment.

Skema yang didukung adalah `http://`, `https://`, `socks4://`, `socks4a://`,
`socks5://`, dan `socks5h://`. Gunakan `socks5h://` agar resolusi nama host
dilakukan oleh proxy. Field bypass/`NO_PROXY` menerima host yang dipisahkan koma,
misalnya `localhost,127.0.0.1,::1,.local`.

URL proxy dapat berisi kredensial, tetapi nilai tersebut dikategorikan sebagai
secret: log menyamarkannya dan ekspor settings portabel membuangnya. Tetap
jaga file settings aktif karena nilainya diperlukan aplikasi saat berjalan.

## DNS

Dropdown **Preset** menyediakan System, Cloudflare `1.1.1.1`/`1.0.0.1`,
Google `8.8.8.8`/`8.8.4.4`, Quad9 `9.9.9.9`, serta endpoint DoH/DoT dan
BebasDNS. Memilih preset otomatis mengisi protocol dan endpoint; pilih
**Custom…** untuk resolver lain. Versi GUI lama hanya menyediakan field bebas,
sehingga alamat seperti `1.1.1.1` sebenarnya dapat diketik tetapi tidak tampak
sebagai pilihan.

| Protocol | Endpoint contoh | Port default | Catatan |
| --- | --- | ---: | --- |
| `system` | kosong | OS | Resolver bawaan sistem operasi |
| `udp` | `1.1.1.1` | 53 | DNS biasa; tidak terenkripsi |
| `tcp` | `1.1.1.1` | 53 | DNS biasa melalui TCP |
| `doh` | `https://cloudflare-dns.com/dns-query` | HTTPS | DNS wire-format melalui HTTPS |
| `dot` | `tls://one.one.one.one` | 853 | DNS-over-TLS; hostname wajib agar sertifikat diverifikasi |

Port `0` memilih port default. Timeout berlaku per query. **Resolusi IPv6 / AAAA**
mengaktifkan pencarian alamat IPv6. Untuk IPv6 dengan port, gunakan tanda kurung,
misalnya `[2606:4700:4700::1111]:853`.

Jika **Fallback ke DNS sistem** aktif, kegagalan resolver khusus akan mencoba
resolver OS. Ini lebih toleran, tetapi dapat membocorkan query ke DNS sistem.
Matikan fallback untuk kebijakan fail-closed. DoH tetap mengikuti profil proxy
aplikasi; `socks5h` mencegah resolusi nama endpoint proxy di sisi lokal.

Preset BebasDNS menggunakan DoH dan hanya memengaruhi proses CYOA Downloader;
setting Windows, browser, router, dan hosts file tidak diubah.

## VPN guard

Aplikasi tidak mengimplementasikan protokol VPN dan tidak mengelola koneksi
provider. Seluruh trafik mengikuti route table OS, sehingga tunnel harus sudah
aktif melalui WireGuard, OpenVPN, Tailscale, NordVPN, atau klien lain.

- `system`: tidak mensyaratkan interface VPN.
- `require`: request downloader diblokir bila interface VPN-like aktif tidak
  terdeteksi.
- **Interface name contains**: filter opsional, misalnya `WireGuard`, `Wintun`,
  atau nama adapter provider. Pencocokan tidak peka huruf besar/kecil.

Deteksi sengaja fail-closed: bila status interface tidak bisa dipastikan, mode
`require` tidak melanjutkan request. VPN guard bukan kill-switch tingkat OS;
aplikasi lain dan koneksi yang tidak melalui pipeline downloader berada di luar
cakupannya.

## CLI

UDP atau TCP:

```powershell
python cyoa_downloader.py URL --dns 1.1.1.1 --dns-protocol udp
python cyoa_downloader.py URL --dns 1.1.1.1 --dns-protocol tcp --dns-timeout 8
```

DoH tanpa fallback DNS sistem:

```powershell
python cyoa_downloader.py URL `
  --dns https://cloudflare-dns.com/dns-query `
  --dns-protocol doh `
  --no-dns-fallback-system
```

DoT, proxy per skema, dan VPN guard:

```powershell
python cyoa_downloader.py URL `
  --dns tls://one.one.one.one --dns-protocol dot --dns-port 853 `
  --proxy-mode manual `
  --proxy-http http://127.0.0.1:8080 `
  --proxy-https socks5h://127.0.0.1:1080 `
  --proxy-bypass localhost,127.0.0.1,::1 `
  --vpn-policy require --vpn-interface WireGuard
```

Gunakan `--dns-ipv6` / `--no-dns-ipv6` dan
`--dns-fallback-system` / `--no-dns-fallback-system` untuk mengubah boolean.
Opsi CLI eksplisit disimpan sebagai profil berikutnya; tanpa opsi eksplisit,
CLI memakai profil yang tersimpan dari GUI.

## Troubleshooting CYOA.CAFE

Halaman `cyoa.cafe/game/...` adalah katalog. Downloader akan membaca record dan
mengikuti `iframe_url` creator yang sebenarnya. Untuk Teen Titans, target creator
adalah `laath.cyoa.cafe/teen-titans-cyoa/`; mengganti DNS atau VPN tidak diperlukan
bila kedua host dapat diakses normal.

Jika gagal:

1. Tekan **Validasi offline** dan periksa apakah VPN guard akan memblokir request.
2. Coba `system` DNS untuk membedakan masalah resolver khusus dari situs.
3. Pastikan mode proxy tidak `manual` dengan endpoint yang sudah mati.
4. Periksa `cyoa_downloader.log` dan `backup_report.txt`; kegagalan satu
   `favicon.ico` yang opsional tidak berarti aset project gagal.
5. Jalankan `python cyoa_downloader.py --dependency-check` untuk memastikan
   `dnspython` dan dukungan SOCKS Requests tersedia.

## Referensi implementasi

- [Requests — proxy per skema dan SOCKS](https://requests.readthedocs.io/en/stable/user/advanced/#proxies)
- [Playwright — konfigurasi proxy browser](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-option-proxy)
- [dnspython — UDP, TCP, dan TLS query](https://dnspython.readthedocs.io/en/stable/query.html)
- [Cloudflare — DNS-over-HTTPS](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/)
- [Cloudflare — DNS-over-TLS](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-tls/)
- [Google Public DNS — DoH](https://developers.google.com/speed/public-dns/docs/doh/)
- [Quad9 — alamat dan layanan resolver](https://docs.quad9.net/services/)
