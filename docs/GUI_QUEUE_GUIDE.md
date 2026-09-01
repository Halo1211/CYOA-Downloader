# GUI Queue Guide — CYOA Downloader v1.0.8

## Mengubah mode tanpa menghapus URL

1. Tambahkan URL ke antrean seperti biasa.
2. Klik badge mode pada baris URL, misalnya `auto`.
3. Pilih mode tujuan dari menu.

Mode yang dipilih langsung disimpan pada job tersebut. URL, urutan antrean,
dan nama file tidak berubah. Jika memilih mode manual, hasil auto-detect lama
dihapus agar job tidak lagi ditampilkan sebagai hasil deteksi otomatis.

## Mengekspor antrean

1. Klik **Export List…** di area Input.
2. Pilih `.csv` untuk format spreadsheet atau `.txt` untuk format teks sederhana.
3. Simpan file, lalu gunakan **Import List…** untuk memuatnya kembali.

CSV memiliki kolom berikut:

```text
url,filename,mode
https://example.com/cyoa/,Example,website_folder
```

Format TXT menggunakan:

```text
https://example.com/cyoa/ | Example | website_folder
```

Mode `auto` juga dipertahankan saat export/import. Field internal antrean,
seperti ID job, tidak ikut diekspor.
