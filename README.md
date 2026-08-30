# tg-downloader

[![Release](https://img.shields.io/github/v/release/crypmiy/tdownloader)](https://github.com/crypmiy/tdownloader/releases)

Pengunduh pesan (dan media) dari channel/grup Telegram yang Anda ikuti, berbasis [Telethon](https://github.com/LonamiWebs/Telethon). Login memakai **akun pengguna** (bukan bot API), sehingga bisa membaca riwayat semua channel yang memang diikuti akun tersebut.

Riwayat perubahan tiap versi ada di [CHANGELOG.md](CHANGELOG.md); versi stabil terbaru bisa diunduh dari halaman [Releases](https://github.com/crypmiy/tdownloader/releases).

Dua cara pakai:

- **Mode interaktif (wizard)** — jalankan tanpa argumen, tinggal jawab pertanyaan. Di akhir wizard dicetak perintah CLI setara yang bisa disimpan untuk otomasi.
- **Mode CLI** — semua opsi lewat argumen, cocok untuk cron/systemd.

## Fitur

- Daftar semua channel, grup, dan **chat bot** pada akun (`--list-channels`), dengan ID dan username — riwayat chat Anda dengan bot bisa diunduh seperti channel biasa.
- Unduh dari satu atau banyak channel sekaligus (`--channel` bisa diulang).
- Rentang waktu fleksibel: dari awal channel, N hari terakhir (`--days`), atau rentang tanggal tertentu (`--from-date` / `--to-date`, waktu lokal).
- Unduh media opsional (`--media`) dengan filter jenis (`--media-types`) dan batas ukuran per file (`--max-media-mb`). Unduhan media idempoten — rerun tidak mengunduh ulang file yang sudah ada.
- Filter kata kunci (`--keyword`), batas jumlah pesan (`--limit`), urutan lama→baru atau baru→lama (`--order`).
- Output per channel di folder terpisah, format JSONL / CSV / TXT (boleh sekaligus).
- **Resume incremental** (`--resume`) — lanjut dari pesan terakhir yang tersimpan lewat `state.json`, output di-append.
- FloodWait Telegram ditangani otomatis (auto-sleep), progres disimpan berkala sehingga aman dihentikan Ctrl+C.

## Persyaratan

- Python ≥ 3.9 (dites di Python 3.12, Ubuntu 24 / JetPack).
- Paket `telethon`.
- API ID & API Hash dari [my.telegram.org](https://my.telegram.org) (menu *API development tools*).

## Instalasi

Ubuntu 24 memakai PEP 668 (*externally-managed-environment*), jadi gunakan venv:

```bash
git clone https://github.com/USERNAME/tdownloader.git
cd tdownloader
python3 -m venv venv
venv/bin/pip install telethon
```

## Kredensial API

Urutan prioritas pembacaan kredensial:

1. Argumen `--api-id` / `--api-hash`
2. Environment variable `TG_API_ID` / `TG_API_HASH`
3. File `tg_config.json` di sebelah skrip (dibuat otomatis oleh wizard bila Anda setuju, permission 600)

Run pertama akan diminta nomor HP + kode OTP (dan password 2FA bila aktif). Sesi login tersimpan di file `.session`, run berikutnya langsung jalan tanpa login ulang.

## Cara pakai

### Mode interaktif

```bash
venv/bin/python tg_downloader.py
```

Wizard akan memandu: pilih channel dari daftar bernomor (bisa cari nama, bisa pilih beberapa, mis. `1,3,5`), rentang waktu, media, format, dst. Sebelum mulai, ditampilkan ringkasan + perintah CLI setara. Di setiap pertanyaan, ketik `b` untuk kembali ke langkah sebelumnya atau `q` untuk keluar.

### Mode CLI

```bash
# daftar channel/grup yang diikuti
venv/bin/python tg_downloader.py --list-channels

# semua pesan dari awal channel, teks saja (JSONL)
venv/bin/python tg_downloader.py --channel @namachannel

# rentang tanggal + foto/video, output JSONL + CSV
venv/bin/python tg_downloader.py --channel @namachannel \
    --from-date 2025-01-01 --to-date 2025-06-30 \
    --media --media-types photo video --format jsonl csv

# 30 hari terakhir, 2 channel, hanya pesan berisi kata kunci
venv/bin/python tg_downloader.py --channel @chan1 --channel=-1001234567890 \
    --days 30 --keyword btc funding --format txt

# unduhan incremental berikutnya
venv/bin/python tg_downloader.py --channel @namachannel --resume
```

> Untuk ID channel negatif gunakan bentuk `--channel=-100xxxxxxxxxx` (dengan tanda `=`), karena tanda minus di awal token akan dibaca sebagai opsi oleh parser.

## Opsi lengkap

| Opsi | Keterangan |
|---|---|
| `-i`, `--interactive` | Paksa mode wizard (otomatis aktif bila tanpa argumen) |
| `--api-id`, `--api-hash` | Kredensial API (alternatif env / `tg_config.json`) |
| `--session NAMA` | Nama file sesi login (default: `tg_session`) |
| `--list-channels` / `--list-all` | Daftar channel & grup / semua dialog, lalu keluar |
| `--channel CH` | Target: `@username`, link t.me, atau ID numerik; boleh diulang |
| `--from-date` / `--to-date` | Rentang tanggal waktu lokal, `YYYY-MM-DD` atau `'YYYY-MM-DD HH:MM'`; `--to-date` tanggal-saja bersifat inklusif |
| `--days N` | Pintasan: N hari terakhir |
| `--tz ZONA` | Zona waktu input/output, mis. `Asia/Jakarta` (default: zona sistem) |
| `--keyword KATA...` | Simpan hanya pesan yang mengandung salah satu kata (case-insensitive) |
| `--limit N` | Berhenti setelah N pesan tersimpan |
| `--order oldest\|newest` | Urutan unduh (default `oldest`; `newest` + `--limit` = N pesan terbaru) |
| `--media` | Unduh juga file media |
| `--media-types JENIS...` | `photo gif video voice audio sticker document` (default: semua) |
| `--max-media-mb MB` | Lewati file lebih besar dari ini (default: 50) |
| `--sleep DTK` | Jeda antar unduhan media, mis. `0.5` |
| `--format jsonl csv txt` | Format output, boleh lebih dari satu (default: `jsonl`) |
| `--output DIR` | Folder output (default: `./tg_download`) |
| `--resume` | Lanjut incremental dari pesan terakhir tersimpan |

## Struktur output

```
tg_download/
└── <nama_channel>/
    ├── messages.jsonl      # 1 objek JSON per baris
    ├── messages.csv        # bila --format csv
    ├── messages.txt        # bila --format txt
    ├── media/              # bila --media (nama file: YYYYMMDD_HHMMSS_<id>.<ext>)
    └── state.json          # id pesan terakhir, dipakai --resume
```

Field per pesan (JSONL/CSV): `id`, `date_local`, `date_utc`, `sender_id`, `sender_name`, `post_author`, `text`, `media_type`, `media_file`, `media_note`, `views`, `forwards`, `replies`, `reply_to_id`, `edit_date_utc`, `grouped_id`, `fwd` (asal forward), `link`, `out` (`true` = pesan kiriman Anda sendiri, berguna untuk memisahkan perintah Anda vs balasan bot).

## Otomasi (systemd timer)

Contoh unduhan incremental harian di Jetson:

```ini
# /etc/systemd/system/tg-downloader.service
[Unit]
Description=Unduh incremental pesan Telegram
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=jetson
WorkingDirectory=/home/jetson/tdownloader
ExecStart=/home/jetson/tdownloader/venv/bin/python tg_downloader.py --channel @namachannel --resume
```

```ini
# /etc/systemd/system/tg-downloader.timer
[Unit]
Description=Jadwal harian tg-downloader

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tg-downloader.timer
```

Alternatif cron:

```
0 6 * * * cd /home/jetson/tdownloader && venv/bin/python tg_downloader.py --channel @namachannel --resume
```

> Catatan: login pertama (OTP) harus dilakukan manual di terminal sebelum dijadwalkan, agar file `.session` sudah terbentuk.

## Keamanan

- **`*.session`** = akses penuh ke akun Telegram Anda. **`tg_config.json`** berisi API hash. Keduanya sudah masuk `.gitignore` — jangan pernah di-commit atau dibagikan. Jika terlanjur bocor: putus sesi lewat Telegram (*Settings → Devices*) dan reset API hash di my.telegram.org.
- Gunakan hanya pada channel/grup yang memang Anda ikuti, dan hindari pola akses berlebihan, sesuai [Terms of Service](https://core.telegram.org/api/terms) Telegram API.

## Lisensi

Dirilis di bawah [Lisensi MIT](LICENSE).
