"""Ekranların ortak durumu: uygulama bağlamı (veritabanı vb.) ve kullanıcının oturumu.

AppContext → uygulama başına bir tane (main.py kurar): veritabanı, şifre kutusu, token önbelleği.
Session    → tarayıcı başına: kim giriş yaptı, dil, son hareket zamanı. NiceGUI'nin app.storage.user'ında
             durur (imzalı çerez + sunucu tarafı depo). Şifre ya da token burada tutulmaz, sadece user_id.

Oturum kuralı (SPEC §10): 15 dk hareketsizlikten sonra düşer. Hareket = tarayıcıda tıklama/tuş
(install_activity_watch). Her sayfa açılışında kullanıcı veritabanından yeniden okunur: bu arada pasif
yapılan ya da silinen kişi bir sonraki adımda dışarı düşer.
"""
import sqlite3
import time
from dataclasses import dataclass

from nicegui import app, ui

from core import users
from core.caller import TokenCache
from core.i18n import DEFAULT_LANG, LANGS
from core.models import User
from core.resources import SecretBox

SESSION_TIMEOUT = 15 * 60       # sn
ACTIVITY_THROTTLE = 30          # sn: tarayıcı en fazla bu sıklıkla "hareket var" der
EXPIRY_CHECK_EVERY = 30         # sn


@dataclass
class AppContext:
    db: sqlite3.Connection
    box: SecretBox
    tokens: TokenCache


def is_expired(last_seen: float, now: float, timeout: float = SESSION_TIMEOUT) -> bool:
    return now - last_seen > timeout


def session_timed_out(store: dict, now: float) -> bool:
    """Açık bir oturumun süresi doldu mu? Oturum yoksa (çıkış yapılmış) HAYIR: kapalı oturum "zaman aşımı" sayılmaz.

    Çıkıştan sonra eski sayfa sunucuda bir süre yaşar (reconnect_timeout) ve zamanlayıcısı çalışmaya devam eder;
    bu kontrol olmasa çıkış yapan kişi giriş sayfasında "oturumun zaman aşımına uğradı" görürdü.
    """
    return "user_id" in store and is_expired(store.get("last_seen", 0), now)


class Session:
    """app.storage.user üzerinde ince bir katman: anahtar adları tek yerde."""

    @staticmethod
    def lang() -> str:
        lang = app.storage.user.get("lang", DEFAULT_LANG)
        return lang if lang in LANGS else DEFAULT_LANG

    @staticmethod
    def set_lang(lang: str) -> None:
        app.storage.user["lang"] = lang

    @staticmethod
    def login(user: User) -> None:
        app.storage.user["user_id"] = user.id
        app.storage.user["last_seen"] = time.time()

    @staticmethod
    def logout(reason: str = "") -> None:
        app.storage.user.pop("user_id", None)
        app.storage.user.pop("last_seen", None)
        if reason:
            app.storage.user["logout_reason"] = reason      # giriş sayfası bir kez gösterir

    @staticmethod
    def pop_logout_reason() -> str:
        return app.storage.user.pop("logout_reason", "")

    @staticmethod
    def touch() -> None:
        if "user_id" in app.storage.user:
            app.storage.user["last_seen"] = time.time()

    @staticmethod
    def current_user(db: sqlite3.Connection) -> User | None:
        """Geçerli oturumun kullanıcısı; süresi dolmuş, pasif ya da silinmişse oturumu kapatır ve None döner."""
        user_id = app.storage.user.get("user_id")
        if user_id is None:
            return None
        if is_expired(app.storage.user.get("last_seen", 0), time.time()):
            Session.logout("session_expired")
            return None
        user = users.get_user(db, user_id)
        if user is None or not user.active:
            Session.logout()
            return None
        Session.touch()
        return user


def require_user(ctx: AppContext) -> User | None:
    """Korumalı sayfaların başında: oturum yoksa giriş sayfasına yollar ve None döner."""
    user = Session.current_user(ctx.db)
    if user is None:
        ui.navigate.to("/login")
    return user


def install_activity_watch() -> None:
    """Tarayıcıdaki tıklama/tuşları "hareket" sayar; oturum süresi dolunca giriş sayfasına yollar."""
    ui.add_body_html("""<script>
      ['click', 'keydown'].forEach(type => document.addEventListener(type, () => emitEvent('gx_activity'), true));
    </script>""")
    ui.on("gx_activity", Session.touch, throttle=ACTIVITY_THROTTLE)

    def check_expiry() -> None:
        if session_timed_out(app.storage.user, time.time()):
            Session.logout("session_expired")
            ui.navigate.to("/login")
    ui.timer(EXPIRY_CHECK_EVERY, check_expiry)
