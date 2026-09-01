# Panduan Pemulihan Gambar Discord — CYOA Downloader v1.0.8

Fitur ini sudah menjadi bagian dari alur download utama CYOA Downloader. Anda
tidak perlu memilih `project.json`, menentukan output JSON, atau menjalankan
alat kedua.

## Cara kerjanya

Saat tombol **Download** biasa dijalankan, program mencari data project dengan
dua cara:

1. membaca `project.json` jika situs menyediakannya; atau
2. mengekstrak object project yang disembunyikan/ditanam di bundle `.js`.

Setelah itu pipeline gambar memindai field seperti `image`,
`backgroundImage`, dan `rowBackgroundImage`. URL berikut dikenali sebagai
attachment Discord:

```text
https://cdn.discordapp.com/attachments/CHANNEL_ID/ATTACHMENT_ID/file.png?...
https://media.discordapp.net/attachments/CHANNEL_ID/ATTACHMENT_ID/file.png?...
```

Program mencoba URL tersebut secara langsung. Jika Discord menjawab bahwa URL
sudah tidak berlaku (`401`, `403`, `404`, atau `410`), program memakai Bot
Token untuk meminta URL baru, lalu mengulangi download. Gambar yang berhasil
tetap ditangani oleh pipeline utama dan referensinya diubah menjadi path lokal
seperti gambar CYOA lainnya.

## Apakah bot harus masuk ke server owner?

Tidak, selama data project sudah mengandung URL
`cdn.discordapp.com/attachments/...` atau
`media.discordapp.net/attachments/...`. Endpoint refresh menerima URL
attachment itu sendiri; program tidak membaca guild, channel, atau message.

Bot baru memerlukan akses server jika yang tersedia hanya link pesan seperti
`https://discord.com/channels/...` dan program harus mencari attachment dari
pesannya. CYOA Downloader tidak melakukan cara tersebut.

## Membuat Bot Token

1. Buka [Discord Developer Portal](https://discord.com/developers/applications).
2. Klik **New Application**, beri nama, lalu buka application tersebut.
3. Pilih menu **Bot**.
4. Buat bot jika diminta, lalu klik **Reset Token** atau **View Token**.
5. Salin token. Jangan membagikannya atau memasukkannya ke file project.

Anda tidak perlu menyalakan privileged gateway intents dan tidak perlu membuat
OAuth2 invite untuk server owner.

## Memasukkan token di GUI

1. Buka **Settings / Maintenance**.
2. Cari bagian **Bot Token Discord** yang langsung tampil di halaman Settings;
   tidak ada panel atau checkbox aktivasi terpisah.
3. Tempel Bot Token.
4. Klik **Simpan**. Token ditulis langsung ke
   `C:\Users\<nama>\.cyoa_downloader\settings.json` tanpa pilihan mode lain.
5. Klik **Tes**, kemudian lakukan download CYOA seperti biasa.

Tidak ada tombol pemulihan khusus. Project yang berasal dari JSON maupun dari
bundle JavaScript melewati alur otomatis yang sama.

## Lokasi token di settings.json

Token tersimpan sebagai teks pada field berikut:

```json
"discord_bot_token": "TOKEN_BOT_DISCORD"
```

File aktif Windows berada di
`C:\Users\<nama>\.cyoa_downloader\settings.json`. Kategori di dalam file diberi
key `_section_...` agar mudah dibaca. Key tersebut hanyalah judul visual dan
tidak perlu diedit. Tidak ada lagi `discord_token_storage`, mode `keyring`, atau
mode `session` untuk Discord.

## CLI

Download normal juga mendukung token khusus untuk satu proses:

```powershell
python cyoa_downloader.py "https://contoh-situs/cyoa/" `
  --discord-token "BOT_TOKEN"
```

Atau gunakan environment variable `DISCORD_BOT_TOKEN`. Untuk mematikan fitur
pada satu proses, gunakan `--no-discord-refresh`.

## Jika gambar tetap gagal

- Pastikan URL memang berada di path `/attachments/` pada salah satu host yang
  didukung.
- Klik **Tes token**; token yang pernah di-reset tidak berlaku lagi.
- Pastikan komputer dapat membuka `discord.com` dan CDN Discord.
- Lihat log download untuk pesan `Discord URL refresh failed`.
- Jika Discord tidak mengembalikan URL baru, attachment asal mungkin sudah
  dihapus permanen. Bot Token tidak dapat memulihkan file yang benar-benar
  sudah dihapus dari Discord.

## Keamanan

Token tidak ditulis ke hasil download atau log, tetapi terlihat sebagai teks di
`settings.json`. Jangan mengunggah, membagikan, atau memasukkan file Settings ke
hasil project. Jika token pernah terlihat orang lain, reset token di Developer
Portal lalu simpan token baru di aplikasi.
