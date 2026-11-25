"""Uygulamanın giriş noktası: ayarları okur, veritabanını açar, sayfaları bağlar, NiceGUI'yi başlatır.

Bu dosya kısa kalır; ekranlar `ui/` altında, iş mantığı `core/` altında yazılır.
"""
import logging

from nicegui import ui

from config import load_settings
from core import resources, users
from core.caller import TokenCache
from core.db import connect
from ui import layout, login, theme
from ui.state import SESSION_TIMEOUT, AppContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gadget")

settings = load_settings()
db = connect(settings.data_dir / "portal.db")
box = resources.SecretBox(settings.secret_key)   # resource kimlik bilgilerini şifreler
resources.ensure_custom_resource(db, box)

if users.ensure_first_admin(db, settings.admin_user, settings.admin_password):
    log.info("İlk admin oluşturuldu: %s", settings.admin_user)
if users.needs_first_admin(db):
    # Kullanıcı yok ve ilk admin bilgileri boş: uygulama açılsa da kimse giremezdi. Sessizce açılmak yerine dur.
    raise RuntimeError("Veritabanında kullanıcı yok ve ADMIN_USER / ADMIN_PASSWORD boş. "
                       ".env'de ikisini doldurup yeniden başlat (bkz. README Kurulum).")

ctx = AppContext(db=db, box=box, tokens=TokenCache())
login.mount(ctx)                                  # /login
layout.mount(ctx)                                 # /  (ana ekran)

ui.run(
    host=settings.host,
    port=settings.port,
    title="Gadget API",
    favicon=theme.FAVICON_SVG,                    # tarayıcı sekmesinde fötr şapka
    storage_secret=settings.secret_key,
    reload=False,
    show=False,
    # Bağlantı kısa süre koparsa (sekme arka planda, ekran uykuda) sunucu sayfayı bu kadar saniye tutar; varsayılan
    # 3 sn'de sayfa silinip tarayıcıda baştan yükleniyordu ("kendi kendine yenileniyor"). Oturum süresiyle aynı.
    reconnect_timeout=SESSION_TIMEOUT,
)
