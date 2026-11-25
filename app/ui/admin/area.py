"""Yönetim alanı (tasarim-v1 §9): admin profilinde Form/Yanıt kapanır, bu alan açılır.

İçinde açılır/kapanır gruplar: Resource'lar, Branding, Kullanıcılar, Roller ve yetkiler.
Her grubun içeriği kendi dosyasındadır; burası sadece sırayı ve çerçeveyi belirler.
"""
from nicegui import ui

from core.i18n import t
from core.models import User
from ui.admin.branding import BrandingAdmin
from ui.admin.resources import ResourcesAdmin
from ui.admin.roles import roles_table
from ui.admin.users import UsersAdmin
from ui.state import AppContext


def admin_area(ctx: AppContext, me: User, lang: str, on_resources_changed) -> None:
    """on_resources_changed: resource eklendi/silindi/seçim değişti → ana ekran func1'i ve menüyü yeniler."""
    groups = [
        ("resources", "dns", lambda: ResourcesAdmin(ctx, lang, on_resources_changed)),
        ("branding", "palette", lambda: BrandingAdmin(ctx, lang)),
        ("users", "group", lambda: UsersAdmin(ctx, me, lang)),
        ("roles", "admin_panel_settings", lambda: roles_table(lang)),
    ]
    # Gruplar kapalı açılır; admin ihtiyacı olanı açar.
    with ui.column().classes("w-full gap-2"):
        for key, icon, body in groups:
            with ui.expansion(t(f"admin.{key}", lang), icon=icon, value=False) \
                    .classes("w-full gx-card").props("header-class=font-semibold"):
                body()

