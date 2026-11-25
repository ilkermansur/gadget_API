"""Branding: ürün adı, logo ve renk paleti (tasarim-v1 §9.2).

Değerler `settings` tablosunda durur. Logo kuralları (LOGO_*) burada; PNG başlığı elle okunur, ek kütüphane
gerekmez. Paletlerin sadece kimlikleri burada; renkleri ekran tarafında (ui/theme.py).
Kurala uymayan girdi ValueError ile ve nedeniyle reddedilir; mevcut değer değişmez.
"""
import base64
import sqlite3
import struct

from core.errors import UserError
from core.models import Branding

DEFAULT_NAME = "Gadget API"
PALETTE_IDS = ["night", "indigo", "ocean", "teal", "emerald", "violet", "burgundy", "copper", "graphite"]

LOGO_MAX_BYTES = 1024 * 1024        # 1 MB
LOGO_MIN_SIZE = (640, 360)
LOGO_RATIO = 16 / 9
LOGO_RATIO_TOLERANCE = 0.01         # %1: 1280×720'yi kabul et, 1280×721'i de; 800×600'ü değil

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def get_branding(conn: sqlite3.Connection) -> Branding:
    values = dict(conn.execute("SELECT key, value FROM settings WHERE key LIKE 'brand.%'").fetchall())
    logo = values.get("brand.logo")
    return Branding(
        name=values.get("brand.name", DEFAULT_NAME),
        palette=values.get("brand.palette", PALETTE_IDS[0]),
        logo_png=base64.b64decode(logo) if logo else None,
    )


def set_name(conn: sqlite3.Connection, name: str) -> None:
    name = name.strip()
    if not name:
        raise UserError("errors.brand_name_empty")
    save(conn, "brand.name", name)


def set_palette(conn: sqlite3.Connection, palette: str) -> None:
    if palette not in PALETTE_IDS:
        raise UserError("errors.bad_palette", palette=palette)
    save(conn, "brand.palette", palette)


def set_logo(conn: sqlite3.Connection, data: bytes) -> None:
    check_logo(data)
    save(conn, "brand.logo", base64.b64encode(data).decode())


def remove_logo(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("DELETE FROM settings WHERE key = 'brand.logo'")


def check_logo(data: bytes) -> None:
    """Logo kurallarını kontrol eder; uymuyorsa nedenini söyleyen ValueError."""
    if len(data) > LOGO_MAX_BYTES:
        raise UserError("errors.logo_too_big", size=f"{len(data) / 1024 / 1024:.1f}")
    if not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        raise UserError("errors.logo_not_png")
    width, height = struct.unpack(">II", data[16:24])        # IHDR: genişlik, yükseklik (4'er bayt)
    if abs(width / height - LOGO_RATIO) > LOGO_RATIO * LOGO_RATIO_TOLERANCE:
        raise UserError("errors.logo_ratio", width=width, height=height)
    if width < LOGO_MIN_SIZE[0] or height < LOGO_MIN_SIZE[1]:
        raise UserError("errors.logo_too_small", width=width, height=height)


def save(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
