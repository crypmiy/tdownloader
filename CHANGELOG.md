# Changelog

Semua perubahan penting proyek ini dicatat di file ini.
Format mengikuti [Keep a Changelog](https://keepachangelog.com/id-ID/1.1.0/),
penomoran versi mengikuti [Semantic Versioning](https://semver.org/lang/id/)
(MAJOR.MINOR.PATCH — MAJOR: perubahan yang memutus kompatibilitas,
MINOR: fitur baru, PATCH: perbaikan bug).

## [1.1.0] - 2026-08-30

### Ditambahkan
- Navigasi wizard: ketik `b` di pertanyaan mana pun untuk kembali ke
  langkah/menu sebelumnya, dan `q` untuk keluar program dengan rapi
  (tidak perlu Ctrl+C lagi).
- Konfirmasi akhir mendukung `b` untuk kembali mengubah pengaturan
  sebelum unduhan dimulai.
- Wizard dirombak menjadi mesin langkah (step machine): `b` di tengah
  suatu langkah mengulang menu langkah itu, `b` di awal langkah mundur
  ke langkah sebelumnya.

## [1.0.0] - 2026-08-10

### Ditambahkan
- Rilis pertama: unduh pesan dari channel, grup, dan chat bot Telegram yang
  ada pada akun, berbasis Telethon (login akun pengguna).
- Mode wizard interaktif (jalan otomatis tanpa argumen): pilih sumber dari
  daftar bernomor dengan pencarian, menu rentang waktu, dan ringkasan +
  perintah CLI setara untuk otomasi.
- Mode CLI lengkap: `--list-channels`, `--channel` (multi), `--from-date` /
  `--to-date` / `--days`, `--tz`, `--keyword`, `--limit`, `--order`.
- Unduh media opsional dengan filter jenis, batas ukuran per file, jeda antar
  unduhan, dan idempoten saat rerun.
- Output per sumber: JSONL / CSV / TXT (boleh sekaligus), termasuk field `out`
  untuk membedakan pesan sendiri vs pesan lawan bicara (berguna di chat bot).
- Resume incremental via `state.json` (`--resume`, output di-append).
- Penanganan FloodWait otomatis dan penyimpanan progres berkala (aman Ctrl+C).
- Kredensial API dari argumen / environment / `tg_config.json` (dibuat wizard,
  permission 600).
- Flag `--version` dan pencetakan versi di awal setiap run.
