#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_downloader.py — Pengunduh pesan Telegram (channel/grup yang Anda ikuti).

Berbasis Telethon (login akun pengguna, BUKAN bot API — bot tidak bisa membaca
riwayat channel kecuali jadi admin; akun pengguna bisa membaca semua channel
yang memang diikuti).

DUA CARA PAKAI:
  A. Mode interaktif (wizard) — tinggal jawab pertanyaan, tanpa hafal opsi:
       python3 tg_downloader.py
     (atau paksa dengan -i / --interactive)
     Di akhir wizard dicetak perintah CLI setara, bisa disimpan utk cron/systemd.

  B. Mode CLI penuh — semua opsi lewat argumen (lihat --help):
       python3 tg_downloader.py --channel @namachannel --days 30 --media

Fitur:
  - --list-channels : daftar channel, grup, dan bot Anda (ID + username)
  - Rentang waktu   : dari awal channel, --from-date/--to-date, atau --days N
  - Media opsional  : --media, filter jenis (--media-types), batas ukuran
  - Filter kata kunci (--keyword), batas jumlah (--limit), urutan (--order)
  - Output          : JSONL / CSV / TXT (boleh sekaligus), per-channel terpisah
  - --resume        : lanjut incremental dari pesan terakhir yang tersimpan

Persiapan (sekali saja):
  1. pip install telethon
  2. Buat API ID & API Hash di https://my.telegram.org ("API development tools")
  3. Kredensial bisa lewat: env TG_API_ID/TG_API_HASH, argumen --api-id/--api-hash,
     atau diketik saat wizard (ditawarkan disimpan ke tg_config.json, chmod 600).
  4. Run pertama diminta nomor HP + kode OTP (dan password 2FA bila aktif).
     Sesi login tersimpan di file .session, run berikutnya langsung jalan.

Catatan: gunakan hanya pada channel/grup yang memang Anda ikuti, dan hindari
pola akses berlebihan (skrip ini sudah otomatis menangani FloodWait Telegram).
"""

import argparse
import asyncio
import csv
import json
import os
import re
import shlex
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

try:
    from telethon import TelegramClient
    from telethon import utils as tg_utils
    from telethon.errors import FloodWaitError
    from telethon.tl.types import MessageService
except ImportError:
    sys.exit("Modul 'telethon' belum terpasang. Jalankan: pip install telethon")

MEDIA_TYPES = ["photo", "gif", "video", "voice", "audio", "sticker", "document"]

CSV_COLS = ["id", "date_local", "date_utc", "sender_id", "sender_name", "text",
            "media_type", "media_file", "media_note", "views", "forwards",
            "replies", "reply_to_id", "edit_date_utc", "forwarded", "link", "out"]

CONFIG_PATH = Path(__file__).resolve().with_name("tg_config.json")

CONTOH = """\
contoh:
  # mode interaktif (wizard) — tanpa perlu hafal opsi
  python3 tg_downloader.py

  # daftar channel/grup yang Anda ikuti
  python3 tg_downloader.py --list-channels

  # semua pesan dari awal channel, teks saja (JSONL)
  python3 tg_downloader.py --channel @namachannel

  # rentang tanggal tertentu + foto/video, output JSONL + CSV
  python3 tg_downloader.py --channel @namachannel \\
      --from-date 2025-01-01 --to-date 2025-06-30 \\
      --media --media-types photo video --format jsonl csv

  # 30 hari terakhir, 2 channel sekaligus, hanya pesan berisi kata kunci
  python3 tg_downloader.py --channel @chan1 --channel=-1001234567890 \\
      --days 30 --keyword btc funding --format txt

  # lanjutkan unduhan berikutnya secara incremental
  python3 tg_downloader.py --channel @namachannel --resume
"""


# ---------------------------------------------------------------- util dasar

def resolve_tz(nama):
    """Kembalikan objek timezone dari nama IANA, atau zona waktu sistem."""
    if nama:
        if ZoneInfo is None:
            sys.exit("[!] Modul zoneinfo tidak tersedia (butuh Python >= 3.9) untuk --tz")
        try:
            return ZoneInfo(nama)
        except Exception:
            sys.exit(f"[!] Zona waktu tidak dikenal: '{nama}' (contoh benar: Asia/Jakarta)")
    return datetime.now().astimezone().tzinfo


def parse_local_dt(s, tz, akhir_hari=False):
    """Terima 'YYYY-MM-DD' atau 'YYYY-MM-DD HH:MM[:SS]' (waktu lokal) -> UTC aware.
    Jika akhir_hari=True dan input hanya tanggal, dianggap 23:59:59 (inklusif).
    Melempar ValueError bila format salah."""
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"Format tanggal tidak valid: '{s}' "
                         f"(gunakan YYYY-MM-DD atau 'YYYY-MM-DD HH:MM')")
    if akhir_hari and len(s) == 10:
        dt = dt + timedelta(days=1) - timedelta(seconds=1)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def fmt_local(dt_utc, tz):
    return dt_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def slugify(teks):
    bersih = re.sub(r"[^\w\-]+", "_", teks, flags=re.UNICODE).strip("_")
    return bersih[:60] or "channel"


def media_type_of(msg):
    """Klasifikasi jenis media sebuah pesan (urutan penting: gif sebelum video)."""
    if msg.photo:
        return "photo"
    if msg.gif:
        return "gif"
    if msg.video:
        return "video"
    if msg.voice:
        return "voice"
    if msg.audio:
        return "audio"
    if msg.sticker:
        return "sticker"
    if msg.document:
        return "document"
    if msg.web_preview:
        return "webpage"
    return None


def fwd_summary(msg):
    """Ringkasan asal pesan forward (jika ada)."""
    f = msg.fwd_from
    if not f:
        return None
    out = {}
    for attr in ("from_name", "post_author"):
        v = getattr(f, attr, None)
        if v:
            out[attr] = v
    if getattr(f, "date", None):
        out["date_utc"] = f.date.isoformat()
    if getattr(f, "from_id", None) is not None:
        out["from_id"] = str(f.from_id)
    if getattr(f, "channel_post", None):
        out["channel_post"] = f.channel_post
    return out or {"forwarded": True}


# ---------------------------------------------------------------- config

def muat_config():
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def simpan_config(data):
    CONFIG_PATH.write_text(json.dumps(data, indent=1))
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass
    print(f"[i] Kredensial tersimpan: {CONFIG_PATH}")


# ---------------------------------------------------------------- prompt

def tanya(prompt, default=None):
    """Prompt teks sederhana. Enter = default."""
    suf = f" [{default}]" if default not in (None, "") else ""
    try:
        j = input(f"{prompt}{suf}: ").strip()
    except EOFError:
        j = ""
    return j if j else (default or "")


def tanya_ya(prompt, default=False):
    """Prompt ya/tidak. Enter = default."""
    d = "Y/t" if default else "y/T"
    j = tanya(f"{prompt} ({d})", "").lower()
    if not j:
        return default
    return j.startswith("y")


# ---------------------------------------------------------------- output

class Writers:
    """Penulis output streaming: JSONL / CSV / TXT. Mendukung append untuk --resume."""

    def __init__(self, out_dir, formats, tz, append):
        self.tz = tz
        mode = "a" if append else "w"
        self.jf = self.cf = self.tf = None
        self.cw = None
        if "jsonl" in formats:
            self.jf = open(out_dir / "messages.jsonl", mode, encoding="utf-8")
        if "csv" in formats:
            p = out_dir / "messages.csv"
            tulis_header = not (append and p.exists() and p.stat().st_size > 0)
            self.cf = open(p, mode, encoding="utf-8", newline="")
            self.cw = csv.writer(self.cf)
            if tulis_header:
                self.cw.writerow(CSV_COLS)
        if "txt" in formats:
            self.tf = open(out_dir / "messages.txt", mode, encoding="utf-8")

    def write(self, rec):
        if self.jf:
            self.jf.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if self.cw:
            row = dict(rec)
            row["forwarded"] = (json.dumps(rec["fwd"], ensure_ascii=False)
                                if rec.get("fwd") else "")
            self.cw.writerow([row.get(c) if row.get(c) is not None else ""
                              for c in CSV_COLS])
        if self.tf:
            try:
                waktu = datetime.fromisoformat(rec["date_local"]).strftime(
                    "%Y-%m-%d %H:%M:%S %Z")
            except Exception:
                waktu = rec["date_local"]
            self.tf.write(f"[{waktu}] #{rec['id']} {rec.get('sender_name') or ''}\n")
            if rec.get("text"):
                self.tf.write(rec["text"] + "\n")
            if rec.get("media_type"):
                info = rec.get("media_file") or rec.get("media_note") or "tidak diunduh"
                self.tf.write(f"(media: {rec['media_type']} -> {info})\n")
            self.tf.write("-" * 60 + "\n")

    def flush(self):
        for f in (self.jf, self.cf, self.tf):
            if f:
                f.flush()

    def close(self):
        for f in (self.jf, self.cf, self.tf):
            if f:
                f.close()


# ---------------------------------------------------------------- telegram

async def resolve_entity(client, ref, cache):
    """Terima @username, link t.me, ID numerik, atau entity langsung."""
    if not isinstance(ref, (str, int)):
        return ref  # sudah berupa entity (dari mode interaktif)
    ref = str(ref).strip()
    kandidat = []
    if re.fullmatch(r"-?\d+", ref):
        kandidat.append(int(ref))
        if not ref.startswith("-"):
            # izinkan ID channel tanpa awalan -100
            kandidat.append(int(f"-100{ref}"))
    else:
        kandidat.append(ref)

    for muat_dialog in (False, True):
        if muat_dialog and cache.get("dialogs") is None:
            cache["dialogs"] = await client.get_dialogs()
        for k in kandidat:
            try:
                return await client.get_entity(k)
            except Exception:
                continue

    # usaha terakhir: cocokkan dengan judul dialog
    if cache.get("dialogs") is None:
        cache["dialogs"] = await client.get_dialogs()
    low = ref.lstrip("@").lower()
    for d in cache["dialogs"]:
        if (d.name or "").lower() == low:
            return d.entity
    raise SystemExit(f"[!] Channel/grup tidak ditemukan: '{ref}'. "
                     f"Cek daftarnya dengan --list-channels")


async def cetak_daftar(client, semua):
    """Cetak daftar dialog: channel & grup (atau semua jika 'semua'=True)."""
    print(f"{'ID':>16}  {'JENIS':<8}  {'USERNAME':<26}  NAMA")
    print("-" * 80)
    n = 0
    async for d in client.iter_dialogs():
        is_bot = d.is_user and getattr(d.entity, "bot", False)
        if not semua and not (d.is_channel or d.is_group or is_bot):
            continue
        uname = getattr(d.entity, "username", None)
        if d.is_group:
            jenis = "grup"
        elif d.is_channel:
            jenis = "channel"
        elif is_bot:
            jenis = "bot"
        else:
            jenis = "chat"
        print(f"{d.id:>16}  {jenis:<8}  {('@' + uname) if uname else '-':<26}  {d.name}")
        n += 1
    print("-" * 80)
    print(f"Total: {n}. Gunakan @username atau ID kolom pertama sebagai --channel.")
    print("Catatan: untuk ID negatif pakai bentuk '=': --channel=-100xxxxxxxxxx")


async def nama_pengirim(msg, cache):
    """Nama tampilan pengirim, dengan cache agar hemat request."""
    sid = msg.sender_id
    if sid is None:
        return None
    if sid not in cache:
        try:
            ent = await msg.get_sender()
            cache[sid] = tg_utils.get_display_name(ent) if ent else None
        except Exception:
            cache[sid] = None
    return cache[sid]


async def unduh_media(msg, media_dir, prefix):
    """Unduh media satu pesan. Kembalikan (Path|None, status).
    Idempoten: jika file dengan prefix sama sudah ada, tidak diunduh ulang."""
    ada = list(media_dir.glob(prefix + ".*"))
    if ada:
        return ada[0], "sudah_ada"
    for _percobaan in range(2):
        try:
            hasil = await msg.download_media(file=str(media_dir / prefix))
            return (Path(hasil), "ok") if hasil else (None, "tanpa_file")
        except FloodWaitError as e:
            tunggu = e.seconds + 5
            print(f"    [FloodWait] tidur {tunggu} dtk...", flush=True)
            await asyncio.sleep(tunggu)
        except Exception as e:
            return None, f"error_{type(e).__name__}"
    return None, "gagal_floodwait"


async def unduh_channel(client, args, ref, tz, from_utc, to_utc, cache):
    """Unduh pesan satu channel sesuai semua opsi."""
    entity = await resolve_entity(client, ref, cache)
    judul = tg_utils.get_display_name(entity) or str(ref)
    uname = getattr(entity, "username", None)
    # link pesan t.me/<uname>/<id> hanya valid utk channel/supergrup publik
    punya_link = bool(uname) and hasattr(entity, "broadcast")
    slug = slugify(uname or judul)

    out_dir = Path(args.output) / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    media_dir = out_dir / "media"
    if args.media:
        media_dir.mkdir(exist_ok=True)

    state_path = out_dir / "state.json"
    min_id = 0
    if args.resume and state_path.exists():
        try:
            min_id = int(json.loads(state_path.read_text()).get("last_id", 0))
        except Exception:
            min_id = 0

    print(f"\n=== {judul} ({'@' + uname if uname else 'tanpa username'}) ===")
    print(f"    Output : {out_dir}/")
    if min_id:
        print(f"    Resume : lanjut setelah pesan id {min_id}")
    rentang = (f"{fmt_local(from_utc, tz) if from_utc else 'awal'} s/d "
               f"{fmt_local(to_utc, tz) if to_utc else 'sekarang'}")
    print(f"    Rentang: {rentang} | urutan: {args.order}")

    # Parameter iterasi.
    # order=oldest  : reverse=True, offset_date = batas bawah (lama -> baru)
    # order=newest  : offset_date = batas atas (baru -> lama)
    kwargs = {}
    if min_id:
        kwargs["min_id"] = min_id
    if args.order == "oldest":
        kwargs["reverse"] = True
        if from_utc:
            kwargs["offset_date"] = from_utc - timedelta(seconds=1)
    else:
        if to_utc:
            kwargs["offset_date"] = to_utc + timedelta(seconds=1)

    writers = Writers(out_dir, args.format, tz, append=bool(args.resume))
    kw_lower = [k.lower() for k in (args.keyword or [])]
    sender_cache = {}
    n_scan = n_simpan = n_media = n_lewati_kw = n_lewati_ukuran = 0
    last_id = min_id
    t0 = time.time()

    def simpan_state():
        state_path.write_text(json.dumps(
            {"channel": judul, "last_id": last_id,
             "updated_utc": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False, indent=1))

    try:
        async for msg in client.iter_messages(entity, **kwargs):
            if isinstance(msg, MessageService):
                continue  # lewati pesan servis (join/pin/dll)

            # batas rentang tanggal -> berhenti dini
            if args.order == "oldest":
                if to_utc and msg.date > to_utc:
                    break
            else:
                if from_utc and msg.date < from_utc:
                    break

            n_scan += 1
            last_id = max(last_id, msg.id)
            teks = msg.message or ""

            if kw_lower and not any(k in teks.lower() for k in kw_lower):
                n_lewati_kw += 1
            else:
                # ---- media (opsional)
                mtype = media_type_of(msg)
                media_rel = None
                media_note = None
                if args.media and mtype and mtype in args.media_types:
                    ukuran = getattr(msg.file, "size", None) if msg.file else None
                    if ukuran and ukuran > args.max_media_mb * 1024 * 1024:
                        media_note = f"dilewati_ukuran_{ukuran / 1e6:.1f}MB"
                        n_lewati_ukuran += 1
                    else:
                        prefix = f"{msg.date:%Y%m%d_%H%M%S}_{msg.id}"
                        path, status = await unduh_media(msg, media_dir, prefix)
                        if path:
                            media_rel = str(Path("media") / path.name)
                            if status == "ok":
                                n_media += 1
                                if args.sleep > 0:
                                    await asyncio.sleep(args.sleep)
                        else:
                            media_note = status

                # ---- rekam pesan
                sname = await nama_pengirim(msg, sender_cache)
                rec = {
                    "id": msg.id,
                    "date_utc": msg.date.isoformat(),
                    "date_local": msg.date.astimezone(tz).isoformat(),
                    "sender_id": msg.sender_id,
                    "sender_name": sname,
                    "post_author": msg.post_author,
                    "text": teks,
                    "media_type": mtype,
                    "media_file": media_rel,
                    "media_note": media_note,
                    "views": msg.views,
                    "forwards": msg.forwards,
                    "replies": msg.replies.replies if msg.replies else None,
                    "reply_to_id": msg.reply_to_msg_id,
                    "edit_date_utc": msg.edit_date.isoformat() if msg.edit_date else None,
                    "grouped_id": msg.grouped_id,
                    "fwd": fwd_summary(msg),
                    "link": f"https://t.me/{uname}/{msg.id}" if punya_link else None,
                    "out": bool(msg.out),
                }
                writers.write(rec)
                n_simpan += 1
                if args.limit and n_simpan >= args.limit:
                    break

            if n_scan % 200 == 0:
                print(f"    [{n_scan:>6}] {msg.date.astimezone(tz):%Y-%m-%d %H:%M} | "
                      f"tersimpan {n_simpan} | media {n_media}", flush=True)
                writers.flush()
                simpan_state()

    except KeyboardInterrupt:
        print("\n[!] Dihentikan (Ctrl+C) — menyimpan progres...")
        raise
    finally:
        writers.close()
        simpan_state()
        durasi = time.time() - t0
        print(f"    Selesai : {n_simpan} pesan tersimpan dari {n_scan} dipindai "
              f"({durasi:.1f} dtk)")
        if args.media:
            print(f"    Media   : {n_media} diunduh, {n_lewati_ukuran} dilewati (ukuran)")
        if kw_lower:
            print(f"    Keyword : {n_lewati_kw} pesan tidak cocok, dilewati")


# ---------------------------------------------------------------- wizard

def bangun_perintah(ns, dialogs, from_str, to_str, days):
    """Susun perintah CLI setara dari pilihan wizard (utk otomasi/cron)."""
    p = ["python3", "tg_downloader.py"]
    for d in dialogs:
        uname = getattr(d.entity, "username", None)
        if uname:
            p += ["--channel", f"@{uname}"]
        else:
            p.append(f"--channel={tg_utils.get_peer_id(d.entity)}")
    if days:
        p += ["--days", str(days)]
    if from_str:
        p += ["--from-date", from_str]
    if to_str:
        p += ["--to-date", to_str]
    if ns.order != "oldest":
        p += ["--order", ns.order]
    if ns.limit:
        p += ["--limit", str(ns.limit)]
    if ns.keyword:
        p += ["--keyword"] + list(ns.keyword)
    if ns.media:
        p.append("--media")
        if set(ns.media_types) != set(MEDIA_TYPES):
            p += ["--media-types"] + list(ns.media_types)
        if ns.max_media_mb != 50.0:
            p += ["--max-media-mb", f"{ns.max_media_mb:g}"]
    if ns.format != ["jsonl"]:
        p += ["--format"] + list(ns.format)
    if ns.output != "tg_download":
        p += ["--output", ns.output]
    if ns.resume:
        p.append("--resume")
    return " ".join(shlex.quote(x) if (" " in x) else x for x in p)


async def pilih_channel(client, cache):
    """Tampilkan daftar bernomor, dukung pencarian, pilih satu/banyak."""
    if cache.get("dialogs") is None:
        print("[i] Memuat daftar channel/grup ...")
        cache["dialogs"] = await client.get_dialogs()
    kanal = [d for d in cache["dialogs"]
             if d.is_channel or d.is_group
             or (d.is_user and getattr(d.entity, "bot", False))]
    if not kanal:
        print("[!] Akun ini tidak punya channel/grup/bot apa pun.")
        return None

    kata = tanya("Cari nama channel/bot (Enter = tampilkan semua)", "")
    while True:
        tampil = ([d for d in kanal if kata.lower() in (d.name or "").lower()]
                  if kata else kanal)
        if not tampil:
            print(f"[!] Tidak ada yang cocok dengan '{kata}'.")
            kata = tanya("Cari nama channel/bot (Enter = tampilkan semua)", "")
            continue

        print(f"\n{'NO':>4}  {'JENIS':<8} {'USERNAME':<24} NAMA")
        print("-" * 76)
        for i, d in enumerate(tampil, 1):
            uname = getattr(d.entity, "username", None)
            if d.is_group:
                jenis = "grup"
            elif d.is_channel:
                jenis = "channel"
            else:
                jenis = "bot"
            print(f"{i:>4}  {jenis:<8} {('@' + uname) if uname else '-':<24} {d.name}")
        print("-" * 76)

        j = tanya("Pilih nomor (boleh banyak, pisah koma/spasi; "
                  "atau ketik kata pencarian baru)")
        token = [t for t in re.split(r"[,\s]+", j) if t]
        if token and all(t.isdigit() for t in token):
            idx = [int(t) for t in token]
            if all(1 <= x <= len(tampil) for x in idx):
                terpilih = [tampil[x - 1] for x in idx]
                print("[i] Dipilih: " + ", ".join(d.name for d in terpilih))
                return terpilih
            print("[!] Ada nomor di luar jangkauan daftar.")
        else:
            kata = j  # anggap sebagai kata pencarian baru


async def mode_interaktif(client, tz, cache):
    """Wizard: kumpulkan semua opsi lewat tanya-jawab. Kembalikan
    (namespace, daftar_dialog_terpilih, from_utc, to_utc) atau None bila batal."""
    print("\n================ MODE INTERAKTIF ================")

    # 1) channel
    terpilih = await pilih_channel(client, cache)
    if not terpilih:
        return None

    # 2) rentang waktu
    from_utc = to_utc = None
    days = None
    from_str = to_str = None
    print("\nRentang waktu:")
    print("  1) Dari awal channel sampai sekarang")
    print("  2) N hari terakhir")
    print("  3) Rentang tanggal tertentu")
    pil = tanya("Pilihan", "1")
    if pil == "2":
        while True:
            n = tanya("Berapa hari terakhir", "30")
            if n.isdigit() and int(n) > 0:
                days = int(n)
                from_utc = datetime.now(timezone.utc) - timedelta(days=days)
                break
            print("[!] Masukkan angka bulat > 0.")
    elif pil == "3":
        while True:
            s = tanya("Tanggal mulai, YYYY-MM-DD atau 'YYYY-MM-DD HH:MM' "
                      "(Enter = dari awal)", "")
            if not s:
                break
            try:
                from_utc = parse_local_dt(s, tz)
                from_str = s
                break
            except ValueError as e:
                print(f"[!] {e}")
        while True:
            s = tanya("Tanggal akhir (Enter = sampai sekarang)", "")
            if not s:
                break
            try:
                calon = parse_local_dt(s, tz, akhir_hari=True)
            except ValueError as e:
                print(f"[!] {e}")
                continue
            if from_utc and calon <= from_utc:
                print("[!] Tanggal akhir harus setelah tanggal mulai.")
                continue
            to_utc = calon
            to_str = s
            break

    # 3) resume, urutan, limit
    resume = tanya_ya("Lanjutkan dari unduhan sebelumnya (resume)?", False)
    pil = tanya("Urutan unduh: 1) lama->baru  2) baru->lama", "1")
    order = "newest" if pil.strip() == "2" else "oldest"
    limit = None
    j = tanya("Batas jumlah pesan (Enter = tanpa batas)", "")
    if j.isdigit() and int(j) > 0:
        limit = int(j)

    # 4) kata kunci
    j = tanya("Filter kata kunci, pisah spasi (Enter = tanpa filter)", "")
    keyword = j.split() if j else None

    # 5) media
    media = tanya_ya("Unduh juga file media (foto/video/dokumen)?", False)
    media_types = list(MEDIA_TYPES)
    max_mb = 50.0
    if media:
        j = tanya("Jenis media, pisah spasi (Enter = semua)\n"
                  "  pilihan: " + " ".join(MEDIA_TYPES), "")
        if j:
            valid = [t for t in j.split() if t in MEDIA_TYPES]
            if valid:
                media_types = valid
            else:
                print("[!] Tidak ada jenis valid, dipakai: semua.")
        j = tanya("Batas ukuran per file (MB)", "50")
        try:
            max_mb = float(j)
        except ValueError:
            print("[!] Bukan angka, dipakai 50 MB.")

    # 6) format & folder output
    j = tanya("Format output (jsonl/csv/txt, boleh lebih dari satu, pisah spasi)",
              "jsonl")
    fmt = [f for f in j.split() if f in ("jsonl", "csv", "txt")] or ["jsonl"]
    out = tanya("Folder output", "tg_download")

    ns = argparse.Namespace(
        media=media, media_types=media_types, max_media_mb=max_mb, sleep=0.0,
        format=fmt, output=out, resume=resume, keyword=keyword,
        limit=limit, order=order)

    # ringkasan
    print("\n---------------- RINGKASAN ----------------")
    print("Channel  : " + ", ".join(d.name for d in terpilih))
    if days:
        print(f"Rentang  : {days} hari terakhir")
    else:
        print(f"Rentang  : {fmt_local(from_utc, tz) if from_utc else 'awal'} s/d "
              f"{fmt_local(to_utc, tz) if to_utc else 'sekarang'}")
    print(f"Urutan   : {'lama->baru' if order == 'oldest' else 'baru->lama'}"
          + (f" | limit {limit}" if limit else ""))
    if keyword:
        print("Keyword  : " + " ".join(keyword))
    print("Media    : " + (f"ya ({', '.join(media_types)}; maks {max_mb:g} MB)"
                           if media else "tidak"))
    print(f"Output   : {out}/ | format: {', '.join(fmt)}"
          + (" | resume" if resume else ""))
    print("\nPerintah CLI setara (simpan untuk otomasi/cron):")
    print("  " + bangun_perintah(ns, terpilih, from_str, to_str, days))

    if not tanya_ya("\nMulai unduh sekarang?", True):
        print("[i] Dibatalkan.")
        return None
    return ns, terpilih, from_utc, to_utc


# ---------------------------------------------------------------- CLI

def parse_args():
    p = argparse.ArgumentParser(
        prog="tg_downloader.py",
        description="Unduh pesan (dan media) dari channel/grup Telegram yang Anda "
                    "ikuti. Jalankan TANPA argumen untuk mode interaktif (wizard).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CONTOH)

    p.add_argument("-i", "--interactive", action="store_true",
                   help="Paksa mode interaktif (otomatis aktif bila tanpa argumen)")

    akun = p.add_argument_group("Akun / API")
    akun.add_argument("--api-id",
                      help="API ID dari my.telegram.org (atau env TG_API_ID / "
                           "tg_config.json)")
    akun.add_argument("--api-hash",
                      help="API Hash dari my.telegram.org (atau env TG_API_HASH / "
                           "tg_config.json)")
    akun.add_argument("--session", default="tg_session",
                      help="Nama file sesi login (default: tg_session)")

    apa = p.add_argument_group("Pilih sumber")
    apa.add_argument("--list-channels", action="store_true",
                     help="Tampilkan daftar channel, grup, dan bot Anda, lalu keluar")
    apa.add_argument("--list-all", action="store_true",
                     help="Seperti --list-channels tetapi termasuk chat pribadi/bot")
    apa.add_argument("--channel", action="append", metavar="CH",
                     help="Channel target: @username, link t.me, atau ID numerik. "
                          "Boleh diulang untuk beberapa channel. "
                          "Untuk ID negatif gunakan bentuk: --channel=-100xxxxxxxxxx")

    waktu = p.add_argument_group("Rentang waktu (waktu lokal)")
    waktu.add_argument("--from-date", metavar="TGL",
                       help="Mulai dari tanggal ini: YYYY-MM-DD atau 'YYYY-MM-DD HH:MM'. "
                            "Kosongkan untuk mulai dari pesan paling awal.")
    waktu.add_argument("--to-date", metavar="TGL",
                       help="Sampai tanggal ini (inklusif bila hanya tanggal). "
                            "Kosongkan = sampai sekarang.")
    waktu.add_argument("--days", type=int, metavar="N",
                       help="Pintasan: ambil N hari terakhir (tidak bisa digabung "
                            "--from-date)")
    waktu.add_argument("--tz", metavar="ZONA",
                       help="Zona waktu untuk input tanggal & output, mis. Asia/Jakarta "
                            "(default: zona waktu sistem)")

    saring = p.add_argument_group("Penyaringan & urutan")
    saring.add_argument("--keyword", nargs="+", metavar="KATA",
                        help="Simpan hanya pesan yang mengandung salah satu kata ini "
                             "(case-insensitive)")
    saring.add_argument("--limit", type=int, metavar="N",
                        help="Berhenti setelah N pesan tersimpan")
    saring.add_argument("--order", choices=["oldest", "newest"], default="oldest",
                        help="Urutan unduh: oldest = lama->baru (default), "
                             "newest = baru->lama (cocok dgn --limit utk N pesan terbaru)")

    med = p.add_argument_group("Media")
    med.add_argument("--media", action="store_true",
                     help="Unduh juga file media (foto/video/dokumen/dll)")
    med.add_argument("--media-types", nargs="+", choices=MEDIA_TYPES,
                     default=list(MEDIA_TYPES), metavar="JENIS",
                     help="Jenis media yang diunduh (default: semua). "
                          "Pilihan: " + ", ".join(MEDIA_TYPES))
    med.add_argument("--max-media-mb", type=float, default=50.0, metavar="MB",
                     help="Lewati file media lebih besar dari ini (default: 50 MB)")
    med.add_argument("--sleep", type=float, default=0.0, metavar="DTK",
                     help="Jeda antar unduhan media, mis. 0.5 (default: 0)")

    keluar = p.add_argument_group("Output")
    keluar.add_argument("--format", nargs="+", choices=["jsonl", "csv", "txt"],
                        default=["jsonl"],
                        help="Format output, boleh lebih dari satu (default: jsonl)")
    keluar.add_argument("--output", default="tg_download", metavar="DIR",
                        help="Folder output (default: ./tg_download)")
    keluar.add_argument("--resume", action="store_true",
                        help="Lanjutkan dari pesan terakhir yang tersimpan "
                             "(append ke file output)")
    return p.parse_args(), p


async def amain():
    args, parser = parse_args()
    wizard = args.interactive or len(sys.argv) == 1

    # ---- kredensial API: argumen > env > tg_config.json > tanya (wizard)
    cfg = muat_config()
    api_id = args.api_id or os.environ.get("TG_API_ID") or cfg.get("api_id")
    api_hash = args.api_hash or os.environ.get("TG_API_HASH") or cfg.get("api_hash")
    if not api_id or not api_hash:
        if wizard:
            print("[i] Kredensial API belum ada. Buat dulu di https://my.telegram.org")
            print("    (login -> 'API development tools' -> buat aplikasi apa saja)")
            while not api_id:
                api_id = tanya("API ID  ")
            while not api_hash:
                api_hash = tanya("API Hash")
            if tanya_ya(f"Simpan ke {CONFIG_PATH.name} agar tidak ditanya lagi?", True):
                simpan_config({"api_id": api_id, "api_hash": api_hash})
        else:
            sys.exit(
                "[!] API ID/Hash belum diberikan.\n"
                "    1. Buka https://my.telegram.org -> 'API development tools'\n"
                "    2. Jalankan dengan --api-id & --api-hash, atau set env:\n"
                "       export TG_API_ID=1234567\n"
                "       export TG_API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
                "    Atau jalankan TANPA argumen untuk mode interaktif.")

    tz = resolve_tz(args.tz)

    # ---- rentang waktu utk mode CLI
    from_utc = to_utc = None
    if args.days is not None:
        if args.from_date:
            sys.exit("[!] Pilih salah satu: --days ATAU --from-date")
        from_utc = datetime.now(timezone.utc) - timedelta(days=args.days)
    try:
        if args.from_date:
            from_utc = parse_local_dt(args.from_date, tz)
        if args.to_date:
            to_utc = parse_local_dt(args.to_date, tz, akhir_hari=True)
    except ValueError as e:
        sys.exit(f"[!] {e}")
    if from_utc and to_utc and from_utc >= to_utc:
        sys.exit("[!] --from-date harus lebih awal dari --to-date")

    client = TelegramClient(args.session, int(api_id), api_hash)
    client.flood_sleep_threshold = 24 * 3600  # auto-tidur saat kena FloodWait

    await client.start()  # run pertama: minta no. HP + kode OTP (+password 2FA)
    me = await client.get_me()
    print(f"[i] Login sebagai: {tg_utils.get_display_name(me)}")

    try:
        if args.list_channels or args.list_all:
            await cetak_daftar(client, semua=args.list_all)
            return

        cache = {"dialogs": None}

        if wizard:
            while True:
                hasil = await mode_interaktif(client, tz, cache)
                if hasil:
                    ns, terpilih, f_utc, t_utc = hasil
                    for d in terpilih:
                        await unduh_channel(client, ns, d.entity, tz,
                                            f_utc, t_utc, cache)
                    print("\n[OK] Selesai.")
                if not tanya_ya("\nJalankan unduhan lain?", False):
                    break
            return

        # mode CLI
        if not args.channel:
            parser.error("berikan --channel (atau jalankan tanpa argumen untuk "
                         "mode interaktif, atau --list-channels untuk daftar)")
        for ref in args.channel:
            await unduh_channel(client, args, ref, tz, from_utc, to_utc, cache)
        print("\n[OK] Semua selesai.")
    finally:
        await client.disconnect()


def main():
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print("\n[!] Keluar.")


if __name__ == "__main__":
    main()
