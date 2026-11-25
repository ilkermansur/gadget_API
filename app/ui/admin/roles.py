"""Yönetim · Roller ve yetkiler (tasarim-v1 §9.4): salt okunur tablo.

Rol sırası Operatör ⊂ Geliştirici ⊂ Admin: her yetkinin "en az hangi rol" istediği yazılır, tablo
core/users.py has_role ile doldurulur. Yetkinin gerçekte uygulandığı yer burası değildir (bkz. catalog.can_run,
layout'taki profil sekmeleri); bu tablo kuralı ekranda gösterir.
"""
from nicegui import ui

from core import users
from core.i18n import t

# yetki (locale anahtarı) → en az gereken rol
PERMISSIONS = [
    ("perm_use", "operator"),
    ("perm_views", "operator"),
    ("perm_patch", "operator"),
    ("perm_custom", "developer"),
    ("perm_overlay", "developer"),
    ("perm_func", "developer"),
    ("perm_resources", "admin"),
    ("perm_users", "admin"),
]


def roles_table(lang: str) -> None:
    with ui.grid().classes("w-full items-center gap-x-3 gap-y-0 text-sm").style(
            "grid-template-columns:minmax(220px,1fr) repeat(3, 110px)"):
        ui.label(t("admin.perm", lang)).classes("text-xs font-semibold gx-muted py-2")
        for role in users.ROLES:
            ui.label(t(f"roles.{role}", lang)).classes("text-xs font-semibold gx-muted py-2 text-center")
        cell = "py-1.5 border-t border-[color:var(--gx-border)]"
        for key, minimum in PERMISSIONS:
            ui.label(t(f"admin.{key}", lang)).classes(cell)
            for role in users.ROLES:
                ui.label("✓" if users.has_role(role, minimum) else "—").classes(cell + " text-center")
    ui.label(t("admin.perm_note", lang)).classes("gx-muted text-xs mt-2")
