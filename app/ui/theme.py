"""Renkler ve ortak görünüm (tasarim-v1 §9.2, §10). Renk değeri yazılan TEK yer burası.

Üç tür renk:
  PALETTES      → Branding'den seçilen palet: koyu tonlar (menü, giriş paneli) + vurgu (butonlar, seçili sekme)
  BASE          → tek ortak düzen: zemin, kart, kenar, giriş kutusu, yazı (dark/light yok, saf beyaz yok)
  METHOD/STATUS/LOCATION → sabit anlam renkleri: paletle değişmez (GET hep yeşil, 500 hep kırmızı...)

Ekranlar rengi doğrudan yazmaz; CSS değişkenini kullanır: style="background: var(--gx-side)".
"""
from nicegui import ui

# id → (Türkçe ad, İngilizce ad, vurgu, vurgu-2, koyu, koyu-2, koyu-3)   (kaynak: mockup)
PALETTES = {
    "night":    ("Gece mavisi", "Night blue", "#1D4ED8", "#2563EB", "#0F172A", "#1E293B", "#334155"),
    "indigo":   ("İndigo", "Indigo", "#4338CA", "#4F46E5", "#11132B", "#1C1F42", "#30345F"),
    "ocean":    ("Okyanus", "Ocean", "#0369A1", "#0284C7", "#0A1A2B", "#12283F", "#24405C"),
    "teal":     ("Petrol", "Teal", "#0F766E", "#0D9488", "#0B1E22", "#12303A", "#24474F"),
    "emerald":  ("Zümrüt", "Emerald", "#047857", "#059669", "#0B1F1A", "#143029", "#24443B"),
    "violet":   ("Mor", "Violet", "#6D28D9", "#7C3AED", "#17112B", "#241B3F", "#3A2E5C"),
    "burgundy": ("Bordo", "Burgundy", "#9F1239", "#BE123C", "#1F0E14", "#2E1720", "#482534"),
    "copper":   ("Bakır", "Copper", "#C2410C", "#EA580C", "#1C1410", "#2B201A", "#45342A"),
    "graphite": ("Grafit", "Graphite", "#374151", "#4B5563", "#111111", "#1F1F1F", "#363636"),
}

BASE = {
    "bg": "#D9DFE7",            # sayfa zemini (açık gri-mavi)
    "card": "#EEF2F6",          # kartlar (bir ton açık)
    "border": "#CDD5DF",
    "input": "#F7F9FB",         # giriş kutuları
    "text": "#111827",
    "muted": "#6B7280",
    "side-text": "#E2E8F0",     # koyu zemin üstündeki yazı
    "side-muted": "#94A3B8",
}

# (zemin, yazı) çiftleri
METHOD_COLORS = {"GET": ("#DCFCE7", "#166534"), "POST": ("#DBEAFE", "#1E40AF"), "PUT": ("#FEF3C7", "#92400E"),
                 "PATCH": ("#EDE5FB", "#5B21B6"), "DELETE": ("#FEE2E2", "#991B1B")}
LOCATION_COLORS = {"path": ("#E9DDFB", "#5B21B6"), "query": ("#CDEFEA", "#115E59"),
                   "body": ("#F8E7B8", "#92400E"), "header": ("#E1E6ED", "#374151")}
STATUS_COLORS = {"2xx": ("#DCFCE7", "#166534"), "4xx": ("#FEF3C7", "#92400E"), "5xx": ("#FEE2E2", "#991B1B"),
                 "timeout": ("#FEF3C7", "#92400E"), "connection_error": ("#FEE2E2", "#991B1B")}


# Menüdeki grup ikonlarının (zemin, ikon) renkleri: gruplara sırayla verilir (kaynak: mockup).
GROUP_TINTS = [("#3A2A1C", "#FDBA74"), ("#13304A", "#7DD3FC"), ("#3D1E2A", "#FDA4AF"), ("#173A2C", "#86EFAC"),
               ("#2E2A4A", "#C4B5FD"), ("#3A3416", "#FDE68A")]

# x-gadget tag ikonu → Material ikon adı (bilinmeyen ad → "folder").
GROUP_ICONS = {"plug": "power", "activity": "show_chart", "flask": "science", "server": "dns", "lock": "lock",
               "database": "storage", "bell": "notifications", "user": "person", "settings": "settings"}


# Ürün işareti: beyaz fötr şapka. Kurdele beyaza boyanmaz, maske ile OYULUR: arkadaki zemin içinden görünür.
# Şapkanın çizimi tek yerde (HAT_*); hem sol paneldeki işaret hem tarayıcı sekmesinin ikonu bundan kurulur.
HAT_BAND = "M17.6 31.5 Q32 34.5 46.4 31.5 L46.8 36 Q32 39 17.2 36 Z"
HAT_SHAPES = ["M17 38 C16 26 20 16 26 15 C28.5 14.6 30 17 32 17 C34 17 35.5 14.6 38 15 C44 16 48 26 47 38 Z",
              "M3 39 C3 35 11 34 17 36.5 Q32 41 47 36.5 C53 34 61 35 61 39 C61 44.5 48 48 32 48 C16 48 3 44.5 3 39 Z"]


def hat_svg(background: str = "") -> str:
    """Şapka SVG'si. background verilirse yuvarlak köşeli zemin üzerine çizilir (sekme ikonu: açık renkli sekme
    çubuğunda beyaz şapka tek başına görünmezdi); verilmezse arka plan saydamdır (sol panel)."""
    shapes = "".join(f'<path d="{d}"/>' for d in HAT_SHAPES)
    under = f'<rect width="64" height="64" rx="14" fill="{background}"/>' if background else ""
    return (f'<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">{under}'
            f'<defs><mask id="gx-hat-band"><rect width="64" height="64" fill="white"/>'
            f'<path d="{HAT_BAND}" fill="black"/></mask></defs>'
            f'<g fill="#FFFFFF" mask="url(#gx-hat-band)" transform="translate(0 -2)">{shapes}</g></svg>')


BRAND_ICON_SVG = hat_svg()                        # sol panel: saydam
FAVICON_SVG = hat_svg(background="#0F172A")       # tarayıcı sekmesi: koyu zemin üzerinde


def group_tint(index: int) -> tuple[str, str]:
    return GROUP_TINTS[index % len(GROUP_TINTS)]


def group_icon(name: str) -> str:
    return GROUP_ICONS.get(name, "folder")


def css_variables(palette_id: str) -> str:
    """Seçili paletin ve ortak düzenin CSS değişkenleri (:root bloğu)."""
    _, _, acc, acc2, side, side2, side3 = PALETTES.get(palette_id, PALETTES["night"])
    values = {"acc": acc, "acc2": acc2, "side": side, "side2": side2, "side3": side3, **BASE}
    return ":root {" + " ".join(f"--gx-{name}: {value};" for name, value in values.items()) + "}"


def status_class(status: int | None, outcome: str) -> str:
    if outcome in ("timeout", "connection_error"):
        return outcome
    if status is not None and 200 <= status < 300:
        return "2xx"
    return "5xx" if status is not None and status >= 500 else "4xx"


GLOBAL_CSS = """
body { background: var(--gx-bg); color: var(--gx-text); font-family: Inter, system-ui, -apple-system, 'Segoe UI', sans-serif; font-size: 14px; }
.gx-mono { font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace; }
.gx-card { background: var(--gx-card); border: 1px solid var(--gx-border); border-radius: 10px; }
.gx-muted { color: var(--gx-muted); }
.gx-side { background: var(--gx-side); color: var(--gx-side-text); }
/* Giriş kutusu zemini sadece kenarlıklı alanlarda (pencereler, giriş). Alt çizgili alanlar (form, filtre)
   bulundukları kartın renginde kalır: zemin verilince boşluksuz beyaz blok gibi görünüyorlardı. */
.q-field--outlined:not(.q-field--dark) .q-field__control { background: var(--gx-input); }
/* İşlev bandındaki seçici yuvanın (40 px) içine sığar, yuvanın zemini görünür */
.gx-band-select .q-field__control, .gx-band-select .q-field__native { min-height: 30px; height: 30px; }
.gx-band-select .q-field__append { height: 30px; }
.gx-side .q-field--dark .q-field__native, .gx-side .q-field--dark input::placeholder { color: var(--gx-side-text); }
/* Üst bardaki seçim grupları (profil, dil): alçak ve yazı dikeyde ortalı. Yükseklik hem düğmeye hem iç kutusuna
   verilir; yalnız düğmeye verilince Quasar'ın iç minimum yüksekliği yazıyı aşağı itiyordu. */
.q-btn.gx-seg { min-height: 26px; height: 26px; padding: 0 12px; }
.q-btn.gx-seg .q-btn__content { min-height: 26px; height: 26px; align-items: center; line-height: 1; }
/* Buton yazıları olduğu gibi (Quasar varsayılanı BÜYÜK HARF): "Çalıştır", "Giriş yap" */
.q-btn { text-transform: none; }
.gx-pill { display: inline-flex; align-items: center; padding: 1px 8px; border-radius: 999px; font-size: 11.5px; font-weight: 700; }
"""


def apply_theme(palette_id: str) -> None:
    """Sayfaya paleti uygular. Her sayfanın başında bir kez çağrılır."""
    _, _, acc, acc2, *_ = PALETTES.get(palette_id, PALETTES["night"])
    ui.colors(primary=acc, secondary=acc2)              # Quasar bileşenlerinin (buton, sekme) vurgu rengi
    ui.add_css(css_variables(palette_id) + GLOBAL_CSS)


def pill(text: str, colors: tuple[str, str]):
    """Renkli küçük etiket (metot, konum, durum)."""
    background, foreground = colors
    return ui.label(text).classes("gx-pill").style(f"background:{background};color:{foreground}")


def resource_label(resource, lang: str) -> str:
    """Kaynağın ekrandaki adı. Özel kaynağın veritabanındaki adı sabittir ("Custom"); ekranda dile göre yazılır."""
    from core.i18n import t
    return t("admin.kind_custom", lang) if resource.kind == "custom" else resource.name

